The program's memory between runs, committed automatically by the workflows.

- `processed.json`: scenes already analysed (so nothing is counted or billed twice)
- `pu_usage.json`: Copernicus processing units spent per month
- `persistent.json`: spots that are bright in most scenes (platforms, buoys, long-term anchored ships)
- `passes.json`: predicted Sentinel-1 passes for the AIS recorder
- `ais/`: AIS recordings around passes, deleted after a few days
