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
assert not m.valid({"id": "mouse:x1"}, "action")  # no name

# Interval parsing.
assert m.parse_interval("0.5") == 0.5 and m.parse_interval("0") == m.MIN_INTERVAL
assert m.parse_interval("inf") is None and m.parse_interval("nan") is None
assert m.parse_interval("abc") is None and m.parse_interval(None) is None

print("ok")
