# tests/test_actions_runner.py
import actions


class FakeBackend:
    def __init__(self):
        self.calls = []
    def send_keys(self, keys): self.calls.append(("keys", tuple(keys)))
    def click(self, x, y, button): self.calls.append(("click", x, y, button))
    def sleep(self, seconds): self.calls.append(("sleep", seconds))


def test_runs_steps_in_order():
    be = FakeBackend()
    steps = [
        {"type": "key", "keys": ["char:a"]},
        {"type": "wait", "seconds": 0.5},
        {"type": "click", "x": 10, "y": 20, "button": "left"},
    ]
    actions.run_steps(steps, be)
    assert be.calls == [
        ("keys", ("char:a",)),
        ("sleep", 0.5),
        ("click", 10, 20, "left"),
    ]


def test_stop_flag_aborts_before_next_step():
    be = FakeBackend()
    stop_after = {"n": 1}
    def should_stop():
        return len(be.calls) >= stop_after["n"]
    steps = [{"type": "key", "keys": ["char:a"]}, {"type": "key", "keys": ["char:b"]}]
    actions.run_steps(steps, be, should_stop=should_stop)
    assert be.calls == [("keys", ("char:a",))]


def test_wait_interrupted_by_stop():
    be = FakeBackend()
    def should_stop():
        return any(c[0] == "sleep" for c in be.calls)  # stop after the first slice
    actions.run_steps(
        [{"type": "wait", "seconds": 5}, {"type": "key", "keys": ["char:a"]}],
        be, should_stop=should_stop,
    )
    assert be.calls == [("sleep", 0.1)]  # one slice, then aborted before the key


class FakeSim:
    """Mimics the BridgeApi state adapter: save() snapshots, load() writes back."""
    def __init__(self, snapshot=None):
        self._read = snapshot          # what read_state would return now
        self._slot = None              # the saved quicksave
        self.writes = []               # snapshots passed to write

    def save(self):
        if self._read is not None:
            self._slot = dict(self._read)

    def load(self):
        if self._slot is not None:
            self.writes.append(dict(self._slot))


def test_save_then_load_writes_snapshot():
    sim = FakeSim(snapshot={"lat": 1.0, "alt": 500.0})
    actions.run_steps([{"type": "save_state"}, {"type": "load_state"}], FakeBackend(), sim=sim)
    assert sim.writes == [{"lat": 1.0, "alt": 500.0}]


def test_load_without_save_is_noop():
    sim = FakeSim(snapshot={"lat": 1.0})
    actions.run_steps([{"type": "load_state"}], FakeBackend(), sim=sim)
    assert sim.writes == []


def test_state_steps_without_sim_are_noop():
    # No sim wired (e.g. None source) — must not raise.
    actions.run_steps([{"type": "save_state"}, {"type": "load_state"}], FakeBackend(), sim=None)
