"""Reproduce the fictional arithmetic without an AI or network connection."""
from decimal import Decimal
import json
from pathlib import Path

def compare(observed,baseline):
 observed,baseline=Decimal(str(observed)),Decimal(str(baseline))
 if not observed.is_finite() or not baseline.is_finite() or observed<0 or baseline<=0:
  raise ValueError('finite nonnegative rainfall and positive baseline required')
 return {'difference_mm':str(observed-baseline),'percent_above':str((observed-baseline)/baseline*100),'percent_of':str(observed/baseline*100)}

if __name__=='__main__':
 data=json.loads((Path(__file__).parent/'source-pack.json').read_text())['fictional_input']
 result=compare(data['monthly_rainfall_mm'],data['same_month_1991_2020_normal_mm'])
 assert Decimal(result['difference_mm'])==20 and Decimal(result['percent_above'])==25 and Decimal(result['percent_of'])==125
 print(json.dumps({'fictional':True,'inputs':data,'calculated':result,'claims_not_tested':['local trend','rarity','extreme classification','causal attribution']}))
