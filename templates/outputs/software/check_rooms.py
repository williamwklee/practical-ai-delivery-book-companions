"""Fictional handoff example; no network, no booking, no ticket mutations."""
import csv
from pathlib import Path
def eligible(row):
    return (int(row['seats']) >= 20 and int(row['total_gbp']) <= 150
            and row['step_free'] == 'true' and row['start'] == '09:00' and row['end'] == '12:00')
if __name__ == '__main__':
    source=Path(__file__).resolve().parents[2]/'sources/room-cards.csv'
    with source.open(newline='') as stream:
        print(', '.join(row['room'] for row in csv.DictReader(stream) if eligible(row)))
