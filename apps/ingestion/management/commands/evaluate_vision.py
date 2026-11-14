"""Measure the detail tags read on the technical drawing against the MVP inventory.

Uses the imported thumbnails (mostly technical drawings) of patterns whose tags were filled
from "booklet and drawing", and compares per tag group.
"""

import random
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

from apps.catalog.models import Pattern
from apps.ingestion.vision import drawing_tags


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True)
        parser.add_argument("--sample", type=int, default=40)
        parser.add_argument("--seed", type=int, default=1)
        parser.add_argument("--model", help="Vision model (default: LLM_MODEL)")
        parser.add_argument("--show", type=int, default=0, help="Print N patterns in detail")

    def handle(self, owner, sample, seed, model, show, **options):
        try:
            user = get_user_model().objects.get(username=owner)
        except get_user_model().DoesNotExist:
            raise CommandError(f"User {owner!r} does not exist") from None
        patterns = list(
            Pattern.objects.filter(
                owner=user, legacy_ref__isnull=False, tags_source="Livret et dessin"
            )
            .exclude(thumbnail="")
            .filter(tags__isnull=False)
            .distinct()
            .prefetch_related("tags")
        )
        random.Random(seed).shuffle(patterns)
        groups = defaultdict(lambda: [0, 0, 0])  # group -> truth, proposed, correct
        values = defaultdict(lambda: [0, 0])  # tag -> proposed, correct
        unjudged = defaultdict(int)  # group -> proposals in groups the inventory left empty
        seconds = 0.0
        done = 0
        for pattern in patterns[:sample]:
            with pattern.thumbnail.open("rb") as fh, Image.open(fh) as img:
                answer = drawing_tags(img, model)
            if answer is None:
                raise CommandError("The vision model did not answer (is LLM_URL reachable?)")
            done += 1
            seconds += answer.seconds
            truth = {(t.group, t.value) for t in pattern.tags.all()}
            labelled = {group for group, _ in truth}
            for group, _ in truth:
                groups[group][0] += 1
            for tag in answer.tags:
                if tag[0] not in labelled:
                    # The inventory often notes one or two details only: a proposal in a
                    # group it left empty cannot be judged here.
                    unjudged[tag[0]] += 1
                    continue
                groups[tag[0]][1] += 1
                groups[tag[0]][2] += tag in truth
                values[tag][0] += 1
                values[tag][1] += tag in truth
            if done <= show:
                self.stdout.write(f"\n{pattern.name} ({pattern.thumbnail.name})")
                self.stdout.write(f"   stored:   {sorted(truth)}")
                self.stdout.write(f"   proposed: {sorted(answer.tags)}")

        self.stdout.write(
            f"\n{done} drawings, {seconds:.0f} s ({seconds / max(done, 1):.1f} s each)"
        )
        header = ("group", "truth", "proposed", "correct", "precision", "recall")
        self.stdout.write("{:16} {:>6} {:>9} {:>8} {:>10} {:>7}".format(*header))
        total = [0, 0, 0]
        for group, (truth, proposed, correct) in sorted(groups.items()):
            total = [a + b for a, b in zip(total, (truth, proposed, correct), strict=True)]
            self._row(group, truth, proposed, correct)
        self._row("ALL", *total)
        self.stdout.write(
            "(precision on the groups the inventory filled; not judged: "
            + ", ".join(f"{g} {n}" for g, n in sorted(unjudged.items()))
            + ")"
        )
        self.stdout.write("\nPer value (correct/proposed), worst first:")
        for (group, value), (proposed, correct) in sorted(
            values.items(), key=lambda kv: kv[1][1] / kv[1][0]
        ):
            self.stdout.write(f"   {correct:3}/{proposed:3} {group} : {value}")

    def _row(self, label, truth, proposed, correct):
        precision = f"{correct / proposed:.0%}" if proposed else "-"
        recall = f"{correct / truth:.0%}" if truth else "-"
        self.stdout.write(
            f"{label:16} {truth:6} {proposed:9} {correct:8} {precision:>10} {recall:>7}"
        )
