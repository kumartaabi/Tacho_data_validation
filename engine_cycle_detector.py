"""Engine ON/OFF cycles and GPS distance from Excel tracking history.

Input:  ./input/*.xlsx (columns IST Ts, Rotation, Lat, Lng; optional Uniqueid)
Output: ./output/<input-stem>_Engine_ON_OFF_Cycles.xlsx

Rules:
  RPM > 0 -> ON; RPM = 0, N/A or blank -> OFF.
  A status change or packet gap > --gap-seconds begins a new cycle.
  Haversine distance is added only between successive valid GPS points
  in the SAME ON cycle, with BOTH endpoint packets having RPM > 0.
  OFF packets and intervals across a >60-second gap are excluded.
"""
import argparse
from pathlib import Path
from math import asin, cos, radians, sin, sqrt
from copy import copy
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / 'input'
OUTPUT_DIR = BASE_DIR / 'output'
EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres."""
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(min(1.0, sqrt(a)))


def valid_gps(lat, lng):
    if pd.isna(lat) or pd.isna(lng):
        return False
    return -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


def duration_hms(seconds):
    seconds = max(0, int(round(seconds)))
    return f'{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}'


def process_file(file_path, output_dir, gap_seconds):
    df = pd.read_excel(file_path)
    df.columns = df.columns.astype(str).str.strip()
    required = {'IST Ts', 'Rotation', 'Lat', 'Lng'}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f'{file_path.name}: Missing columns: {sorted(missing)}')

    df['IST Ts'] = pd.to_datetime(df['IST Ts'], errors='coerce')
    df = df.dropna(subset=['IST Ts']).copy()
    if df.empty:
        raise ValueError(f'{file_path.name}: No valid IST Ts timestamps')

    # Handle separate vehicle histories independently when Uniqueid is available.
    if 'Uniqueid' not in df.columns:
        df['Uniqueid'] = 'Vehicle_1'
    df['Uniqueid'] = df['Uniqueid'].fillna('Unknown').astype(str)
    df = df.sort_values(['Uniqueid', 'IST Ts'], kind='stable').reset_index(drop=True)

    rpm = pd.to_numeric(df['Rotation'], errors='coerce').fillna(0)
    df['Engine Status'] = rpm.gt(0).map({True: 'ON', False: 'OFF'})
    df['Lat'] = pd.to_numeric(df['Lat'], errors='coerce')
    df['Lng'] = pd.to_numeric(df['Lng'], errors='coerce')

    grouped = df.groupby('Uniqueid', sort=False)
    df['Time Difference (Seconds)'] = grouped['IST Ts'].diff().dt.total_seconds().fillna(0)
    df['Gap > Threshold'] = df['Time Difference (Seconds)'] > gap_seconds
    previous_status = grouped['Engine Status'].shift()
    df['New Cycle'] = previous_status.isna() | df['Engine Status'].ne(previous_status) | df['Gap > Threshold']
    df['Cycle ID'] = df['New Cycle'].cumsum().astype(int)
    df['Segment ID'] = df['Gap > Threshold'].astype(int).groupby(df['Uniqueid']).cumsum() + 1

    # Each packet contributes the distance from the immediately previous packet
    # only when both packets are in the same continuous ON cycle.
    df['Distance (km)'] = 0.0
    for _, group in df.groupby('Uniqueid', sort=False):
        previous_index = None
        for i in group.index:
            if previous_index is not None:
                prev = df.loc[previous_index]
                curr = df.loc[i]
                if (curr['Cycle ID'] == prev['Cycle ID']
                    and curr['Engine Status'] == 'ON'
                    and prev['Engine Status'] == 'ON'
                    and valid_gps(prev['Lat'], prev['Lng'])
                    and valid_gps(curr['Lat'], curr['Lng'])):
                    df.at[i, 'Distance (km)'] = haversine_km(
                        prev['Lat'], prev['Lng'], curr['Lat'], curr['Lng'])
            previous_index = i

    cycles = []
    for cycle_id, group in df.groupby('Cycle ID', sort=False):
        start = group['IST Ts'].iloc[0]
        last = group['IST Ts'].iloc[-1]
        end = last
        # Time until next packet may be counted for a transition, but never
        # across a gap > threshold or for the final cycle of a vehicle.
        next_index = group.index[-1] + 1
        if next_index < len(df):
            nxt = df.iloc[next_index]
            if nxt['Uniqueid'] == group['Uniqueid'].iloc[0]:
                gap = (nxt['IST Ts'] - last).total_seconds()
                if 0 <= gap <= gap_seconds:
                    end = nxt['IST Ts']
        elapsed = (end - start).total_seconds()
        cycles.append({
            'Cycle ID': int(cycle_id),
            'Uniqueid': group['Uniqueid'].iloc[0],
            'Segment ID': int(group['Segment ID'].iloc[0]),
            'Engine Status': group['Engine Status'].iloc[0],
            'Start Time': start,
            'End Time': end,
            'Duration (Seconds)': elapsed,
            'Duration (HH:MM:SS)': duration_hms(elapsed),
            'Packet Count': len(group),
            'Distance (km)': round(float(group['Distance (km)'].sum()), 3),
        })
    cycles_df = pd.DataFrame(cycles)

    gaps_df = df.loc[df['Gap > Threshold'],
                     ['Uniqueid', 'IST Ts', 'Time Difference (Seconds)', 'Cycle ID', 'Segment ID']].copy()
    gaps_df.insert(1, 'Previous Packet Time', df.groupby('Uniqueid')['IST Ts'].shift().loc[gaps_df.index])
    gaps_df.rename(columns={'IST Ts': 'Current Packet Time'}, inplace=True)

    segments_df = df.groupby(['Uniqueid', 'Segment ID'], sort=False).agg(
        Start_Time=('IST Ts', 'first'),
        End_Time=('IST Ts', 'last'),
        Packet_Count=('IST Ts', 'size'),
        Distance_km=('Distance (km)', 'sum'),
    ).reset_index()
    segments_df.rename(columns={'Start_Time': 'Start Time', 'End_Time': 'End Time',
                                'Packet_Count': 'Packet Count', 'Distance_km': 'Distance (km)'}, inplace=True)
    segments_df['Distance (km)'] = segments_df['Distance (km)'].round(3)

    summary = pd.DataFrame([
        ('Total Packets', len(df)),
        ('Vehicles', df['Uniqueid'].nunique()),
        ('Engine ON Cycles', int((cycles_df['Engine Status'] == 'ON').sum())),
        ('Engine OFF Cycles', int((cycles_df['Engine Status'] == 'OFF').sum())),
        ('Packet Gaps > Threshold', len(gaps_df)),
        ('Total Engine ON Distance (km)', round(float(cycles_df['Distance (km)'].sum()), 3)),
        ('Total Engine ON Duration (hours)', round(float(cycles_df.loc[
            cycles_df['Engine Status'] == 'ON', 'Duration (Seconds)'].sum()) / 3600, 3)),
    ], columns=['Metric', 'Value'])

    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f'{file_path.stem}_Engine_ON_OFF_Cycles.xlsx'
    with pd.ExcelWriter(dest, engine='openpyxl') as writer:
        summary.to_excel(writer, sheet_name='Summary', index=False)
        cycles_df.to_excel(writer, sheet_name='Engine Cycles', index=False)
        gaps_df.to_excel(writer, sheet_name='Packet Gaps', index=False)
        segments_df.to_excel(writer, sheet_name='Packet Segments', index=False)
        df.to_excel(writer, sheet_name='Packet Classification', index=False)
        for sheet in writer.sheets.values():
            sheet.freeze_panes = 'A2'
            sheet.auto_filter.ref = sheet.dimensions
            for cell in sheet[1]:
                font = copy(cell.font)
                font.bold = True
                cell.font = font
            for column in sheet.columns:
                letter = column[0].column_letter
                width = min(36, max(13, max(len(str(cell.value or '')) for cell in list(column)[:100]) + 2))
                sheet.column_dimensions[letter].width = width
    print(f'Completed: {dest} | ON distance: {summary.loc[summary.Metric == "Total Engine ON Distance (km)", "Value"].iloc[0]} km')


def main():
    parser = argparse.ArgumentParser(description='Engine cycles and Haversine distance from input folder')
    parser.add_argument('--input-folder', type=Path, default=INPUT_DIR)
    parser.add_argument('--output-folder', type=Path, default=OUTPUT_DIR)
    parser.add_argument('--gap-seconds', type=float, default=60)
    args = parser.parse_args()
    args.input_folder.mkdir(parents=True, exist_ok=True)
    files = sorted(f for f in args.input_folder.glob('*.xlsx') if not f.name.startswith('~$'))
    if not files:
        print(f'No .xlsx files found in {args.input_folder}')
        return
    for file in files:
        try:
            process_file(file, args.output_folder, args.gap_seconds)
        except Exception as exc:
            print(f'ERROR processing {file.name}: {exc}')


if __name__ == '__main__':
    main()