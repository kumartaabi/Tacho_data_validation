ENGINE CYCLE DETECTOR

1. Put your input Excel files (.xlsx) into the input/ folder.
2. On Ubuntu install the library: python3 -m pip install artifact-tool
3. Open a terminal in the engine_cycle_package folder.
4. Run: python3 engine_cycle_detector.py
5. Find reports in the output/ folder.

The program reads IST Ts and Rotation columns. RPM > 0 = ON;
RPM 0, N/A, blank or invalid = OFF.
A timestamp gap greater than 60 seconds creates a new segment/cycle.
Reports include Summary, Engine Cycles, Packet Gaps,
Packet Segments and Packet Classification.

To use another folder:
python3 engine_cycle_detector.py --input-folder "/my/data" --output-folder "/my/reports"
