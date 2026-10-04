"""Checks the hook decision logic without touching real input: python test_macroclic.py"""

import macroclic as m

m.cfg["trigger"] = m.key_input(0x75, 0x40, False)  # F6
never = lambda: None

# Trigger toggles on DOWN only, auto-repeat DOWNs don't flicker, both swallowed.
assert m.handle("key:117:0", True, never) and m.active
assert m.handle("key:117:0", True, never) and m.active
assert m.handle("key:117:0", False, never) and m.active
assert m.handle("key:117:0", True, never) and not m.active
m.handle("key:117:0", False, never)

# Other inputs pass through.
assert not m.handle("key:65:0", True, never)
assert not m.handle("mouse:left", True, never)

# Capture takes the next DOWN and swallows its UP; left click can't be a trigger.
m.handle("mouse:left", False, never)
m.capture = "trigger"
assert not m.handle("mouse:left", True, lambda: m.mouse_input("left"))
assert m.capture == "trigger"
m.handle("mouse:left", False, never)
assert m.handle("mouse:x2", True, lambda: m.mouse_input("x2"))
assert m.capture is None and m.cfg["trigger"]["id"] == "mouse:x2"
assert m.handle("mouse:x2", False, never) and not m.active
assert not m.handle("mouse:left", False, never)  # its DOWN wasn't swallowed
assert m.handle("mouse:x2", True, never) and m.active

# Escape cancels a capture and is swallowed.
m.capture = "action"
assert m.handle(m.ESCAPE, True, never) and m.capture is None
assert m.handle(m.ESCAPE, False, never)

# A key already held when capture starts: its repeats are not captured.
m.handle("key:32:0", True, never)
m.capture = "trigger"
assert not m.handle("key:32:0", True, never) and m.capture == "trigger"
assert not m.handle("key:32:0", False, never)
m.capture = None

# Enter and numpad Enter are different inputs.
assert m.key_input(13, 0x1C, False)["id"] != m.key_input(13, 0x1C, True)["id"]

# Config validation.
assert m.valid(m.mouse_input("left"), "action")
assert not m.valid(m.mouse_input("left"), "trigger")
assert m.valid({"id": "key:65:0", "name": "A", "scan": 30, "ext": False}, "trigger")
assert not m.valid({"id": "key:65", "name": "A", "scan": 30, "ext": False}, "trigger")
assert not m.valid({"id": "key:65:1", "name": "A", "scan": 30, "ext": False}, "trigger")
assert not m.valid({"id": "mouse:nope"}, "action")
assert not m.valid({"id": "key:65"}, "action")
assert not m.valid(None, "action") and not m.valid({"id": 3}, "action")

# Names are rebuilt in English, whatever the saved file says.
assert m.renamed({"id": "mouse:x1", "name": "Bouton souris 4"})["name"] == "Mouse button 4"
assert m.renamed({"id": "key:13:1", "scan": 0x1C, "ext": True})["name"] == "Num Enter"
assert m.key_input(0x75, 0x40, False)["name"] == "F6"
assert m.key_input(0x41, 0x10, False)["name"] == "A"

# Interval parsing.
assert m.parse_interval("0.5") == 0.5 and m.parse_interval("0") == m.MIN_INTERVAL
assert m.parse_interval("inf") is None and m.parse_interval("nan") is None
assert m.parse_interval("abc") is None and m.parse_interval(None) is None
assert m.parse_interval(True) is None and m.parse_interval("0,5") == 0.5

# Anti-detection percent.
assert m.parse_jitter("50") == 50 and m.parse_jitter("12.6") == 13
assert m.parse_jitter("0") == 1 and m.parse_jitter("500") == m.MAX_JITTER
assert m.parse_jitter("inf") is None and m.parse_jitter("abc") is None

# Anti-detection spreads the gap after each press evenly, never below zero.
m.cfg.update(interval=1.0, random=False, jitter=50)
gap = 1.0 - m.PRESS_TIME
assert m.next_gap() == gap
m.cfg["random"] = True
waits = [m.next_gap() for _ in range(1000)]
assert all(gap * 0.5 <= w <= gap * 1.5 for w in waits)
assert min(waits) < gap * 0.6 and max(waits) > gap * 1.4
m.cfg.update(interval=m.MIN_INTERVAL, jitter=m.MAX_JITTER)
assert min(m.next_gap() for _ in range(1000)) > 0

print("ok")
