#!/usr/bin/env sh
# Local UI demo without MT5: SIMULATED terminal replaying SYNTHETIC bars, separate database (data/sim_state.sqlite).
cd "$(dirname "$0")/.."
exec metals-lab --sim serve --sim-speed 1.0
