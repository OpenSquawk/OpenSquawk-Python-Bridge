from simulator import DummyFlight, FlightState


def test_dummy_conforms_to_source_interface():
    src = DummyFlight()
    src.open()                      # must exist (alias/reset)
    state = src.sample()
    assert isinstance(state, FlightState)
    assert state.connected is True
    assert state.aircraft           # non-empty label
    src.close()                     # must exist, no-op


def test_flightstate_has_new_fields():
    fs = FlightState(raw={}, phase="Parked", progress=0.0,
                     flight_active=False, aircraft="X", connected=True)
    assert fs.aircraft == "X"
    assert fs.connected is True
