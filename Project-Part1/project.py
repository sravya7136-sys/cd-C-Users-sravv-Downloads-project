#!/usr/bin/env python3
"""
kvstore.py

Simple append-only persistent key-value store.
Usage (interactive):
  $ python project.py
  SET foo bar
  OK
  GET foo
  bar
  EXIT

File format (binary, repeated records):
  [4 bytes key_len][4 bytes val_len][key bytes][value bytes]

Index:
  In-memory index is a list of entries: (key_bytes, file_offset, val_len)
  We search from the end to implement last-write-wins efficiently.
"""

import os
import sys
import struct

DB_FILENAME = "data.db"
HEADER_FMT = ">II"        # big-endian two unsigned ints: key_len, val_len
HEADER_SIZE = struct.calcsize(HEADER_FMT)


class SimpleIndex:
    """
    In-memory index implemented without using dict/map.
    It's a list of tuples: (key_bytes, offset, val_len)
    For GET, we search from back to front to find the latest entry (last-write-wins).
    For rebuilding, we maintain entries in append order (older -> newer).
    """
    def __init__(self):
        self._entries = []  # list of (key_bytes, offset, val_len)

    def update(self, key_bytes, offset, val_len):
        # Append new index entry. We do not remove older entries here.
        self._entries.append((key_bytes, offset, val_len))

    def get(self, key_bytes):
        # search from end to start for last-write-wins
        for k, offset, val_len in reversed(self._entries):
            if k == key_bytes:
                return offset, val_len
        return None

    def __len__(self):
        return len(self._entries)


def ensure_db_exists():
    if not os.path.exists(DB_FILENAME):
        open(DB_FILENAME, "ab").close()


def fsync_file(f):
    f.flush()
    os.fsync(f.fileno())


def replay_log(index):
    """Read data.db sequentially and populate index."""
    if not os.path.exists(DB_FILENAME):
        return
    with open(DB_FILENAME, "rb") as f:
        pos = 0
        while True:
            header = f.read(HEADER_SIZE)
            if not header or len(header) < HEADER_SIZE:
                break  # end of file or incomplete header
            key_len, val_len = struct.unpack(HEADER_FMT, header)
            key = f.read(key_len)
            if len(key) < key_len:
                break  # incomplete record at EOF
            value = f.read(val_len)
            if len(value) < val_len:
                break  # incomplete record at EOF
            # record begins at 'pos'; useful if we want to read from file on GET
            index.update(key, pos + HEADER_SIZE + key_len, val_len)  # offset to the value bytes
            pos += HEADER_SIZE + key_len + val_len
            # seek is unnecessary because read progresses; continue
    # replay finished


def do_set(f, index, key_str, value_str):
    key = key_str.encode("utf-8")
    value = value_str.encode("utf-8")
    key_len = len(key)
    val_len = len(value)
    # record file offset for value AFTER the header+key, so we can seek to and read value later
    # compute upcoming record's starting position
    offset_before = f.tell()
    # write header
    f.write(struct.pack(HEADER_FMT, key_len, val_len))
    # write key and value
    f.write(key)
    f.write(value)
    # persist immediately
    fsync_file(f)
    # index entry: store offset to value bytes (so we can read only the value on GET)
    value_offset = offset_before + HEADER_SIZE + key_len
    index.update(key, value_offset, val_len)
    # respond
    print("OK")
    sys.stdout.flush()


def do_get(f, index, key_str):
    key = key_str.encode("utf-8")
    found = index.get(key)
    if not found:
        print("NOTFOUND")
        sys.stdout.flush()
        return
    value_offset, val_len = found
    # read value from file
    f.flush()  # ensure file state is consistent
    # open a dedicated file descriptor for reading to avoid moving append pointer (optional)
    with open(DB_FILENAME, "rb") as rf:
        rf.seek(value_offset)
        value = rf.read(val_len)
        try:
            s = value.decode("utf-8")
        except Exception:
            # binary fallback
            s = value.decode("latin-1")
        print(s)
        sys.stdout.flush()


def main():
    ensure_db_exists()
    index = SimpleIndex()
    # rebuild index by replaying the append-only log
    replay_log(index)

    # open main DB file in append+binary mode for writing
    with open(DB_FILENAME, "ab+") as f:
        # Ensure file pointer is at end for appends
        f.seek(0, os.SEEK_END)

        # Read commands from stdin until EXIT or EOF
        # Commands are line-oriented. SET <key> <value...>
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            parts = line.split(" ", 2)  # allow value to contain spaces (split max 2)
            cmd = parts[0].upper()
            if cmd == "EXIT":
                # exit cleanly
                print("BYE")
                sys.stdout.flush()
                return
            elif cmd == "SET":
                if len(parts) < 3:
                    print("ERR: SET requires key and value")
                    sys.stdout.flush()
                    continue
                key = parts[1]
                value = parts[2]
                do_set(f, index, key, value)
            elif cmd == "GET":
                if len(parts) < 2:
                    print("ERR: GET requires a key")
                    sys.stdout.flush()
                    continue
                key = parts[1]
                do_get(f, index, key)
            else:
                print("ERR: Unknown command")
                sys.stdout.flush()


if __name__ == "__main__":
    main()
