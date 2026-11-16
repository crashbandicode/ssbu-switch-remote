#!/usr/bin/python3
"""DBI 912 main-menu helpers for the pad route (docs/FTP-PLAN-r1.md): where the black highlight bar is.

DBI draws its menu as text rows on dark blue; the selected row is a black bar. Entry rows (1280x720, measured on
ssbu-switch-remote-work/ftp-r1/shots/34-R5-open-dbi.png, DBI 912-en):"""
from PIL import Image

ENTRIES = [  # (label, text centre y)
    ('Browse SD card', 103), ('Install via DBIbackend', 135), ('Browsing HTTP/FTP/Github server...', 167),
    ('Installed games', 199), ('Installed homebrew', 215), ('Tools', 231), ('Tickets', 263), ('Save Data', 279),
    ('Start MTP connection', 311), ('Start FTP server...', 327), ('Starting HTTP server...', 343),
    ('Activity Log', 375), ('DBI Settings', 391), ('Exit', 407)]


def highlighted(path):
    """(index, label) of the entry under the black bar, or (None, None)."""
    with Image.open(path) as im:
        im = im.convert('RGB').resize((1280, 720))
        px = im.load()
        rows = []
        for y in range(90, 420):
            black = sum(1 for x in range(400, 1200, 8) if max(px[x, y]) < 30)
            if black > 90:  # most of the bar's right part is black (the text is on the left)
                rows.append(y)
        if not rows:
            return None, None
        mid = (min(rows) + max(rows)) / 2
        i = min(range(len(ENTRIES)), key=lambda k: abs(ENTRIES[k][1] - mid))
        if abs(ENTRIES[i][1] - mid) > 6:
            return None, None
        return i, ENTRIES[i][0]
