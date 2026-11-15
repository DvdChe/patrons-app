"""PDFium is not thread-safe: two threads calling into it at once corrupt its heap and crash
the process. Every PDFium call (open, read, render, close) must hold this lock. Keep slow work
that does not touch PDFium (OCR, encoding) outside it."""

import threading

LOCK = threading.RLock()
