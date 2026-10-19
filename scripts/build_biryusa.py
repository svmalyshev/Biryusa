"""Build the Biryusa field map from source catalogues and georeferenced tiles.

Run with the bundled Python runtime (pdfplumber, Pillow). Source files are
read-only; generated assets are kept inside the Biryusa portal directory.
"""
import csv
import io
import json
import math
import re
import shutil
import sqlite3
from pathlib import Path

import pdfplumber
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parents[1] / '2025_Бирюса'
MAPS = SOURCE / 'Maps'


def clean(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def coordinates(value):
    matches = re.findall(r'(\d+)°\s*(\d+)[\x27′]\s*(\d+(?:[.,]\d+)?)["″]\s*([NE])', value)
    assert len(matches) == 2, value
    result = {direction: int(d)+int(m)/60+float(s.replace(',', '.'))/3600
              for d, m, s, direction in matches}
    assert 53 < result['N'] < 56 and 97 < result['E'] < 100
    return result['N'], result['E']


def load_samples():
    samples = []
    for year in (2024, 2025):
        path = SOURCE / ('samples_' + str(year) + '_Biryusa_red.pdf')
        with pdfplumber.open(path) as pdf:
            table = pdf.pages[0].extract_tables()[0]
            page_text = pdf.pages[0].extract_text()
        records, active = [], None
        for row in table:
            values = [clean(' '.join(v for v in row[i:i+3] if v)) for i in (0,3,6,9)]
            if re.fullmatch(r'(BR24|SM25)-\d+(?:/\d+)?', values[0]):
                active = values
                records.append(active)
            elif active and any(values):
                for i, value in enumerate(values):
                    if value:
                        active[i] = clean(active[i] + ' ' + value)
        assert len(records) == (5 if year == 2024 else 12), records
        campaign_note = clean(page_text.split('*',1)[1]) if year == 2025 and '*' in page_text else ''
        for sample, position, analysis, note in records:
            lat, lon = coordinates(position)
            samples.append({'sample':sample,'point_id':sample.split('/')[0], 'year':year,
                            'lat':lat,'lon':lon,'position_raw':position,'analysis':analysis,
                            'description':note,'campaign_note':campaign_note,
                            'source':path.name,'source_page':1,
                            'source_url':'data/catalog_'+str(year)+'.pdf#page=1'})
        shutil.copyfile(path, ROOT / 'data' / ('catalog_'+str(year)+'.pdf'))
    assert len({s['sample'] for s in samples}) == len(samples)
    (ROOT/'data'/'samples.json').write_text(json.dumps(samples, ensure_ascii=False, indent=2),encoding='utf-8')
    with (ROOT/'data'/'samples.csv').open('w',encoding='utf-8-sig',newline='') as output:
        writer=csv.DictWriter(output,fieldnames=list(samples[0]));writer.writeheader();writer.writerows(samples)
    return samples


def database(path):
    return sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)


def export_layer(layer_id, name, sources, maxzoom=14, enabled=False):
    output = ROOT/'maps'/layer_id
    stamps=[{'file':str(p.relative_to(SOURCE)),'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in sources]
    meta_path=output/'metadata.json'
    if meta_path.exists():
        previous=json.loads(meta_path.read_text())
        if previous.get('source_stamp')==stamps and previous.get('export_maxzoom')==maxzoom:
            return previous
    output.mkdir(parents=True,exist_ok=True)
    seen=set(); bounds=[180.,90.,-180.,-90.]; zooms=set(); sizes=set()
    for path in sources:
        with database(path) as db:
            if path.suffix=='.mbtiles':
                # MBTiles uses TMS rows (south-to-north); the browser uses XYZ.
                rows=((z,x,2**z-1-y,data) for z,x,y,data in db.execute('select zoom_level,tile_column,tile_row,tile_data from tiles where zoom_level<=?',(maxzoom,)))
            else:
                # RMaps SQLite stores z=17-web_zoom; x/y already use XYZ.
                rows=((17-z,x,y,data) for x,y,z,data in db.execute('select x,y,z,image from tiles'))
            for z,x,y,data in rows:
                if z>maxzoom:continue
                key=(z,x,y)
                if key in seen:continue
                seen.add(key);zooms.add(z)
                with Image.open(io.BytesIO(data)) as im:
                    sizes.add(im.size)
                    assert im.size==(256,256)
                    suffix='.png' if data.startswith(b'\x89PNG') else '.jpg'
                    # Keep the original PNG alpha and colours, including collars.
                    assert suffix=='.png', 'Unexpected map encoding: '+path.name
                tile=output/str(z)/str(x)/(str(y)+suffix)
                tile.parent.mkdir(parents=True,exist_ok=True);tile.write_bytes(data)
                west=x/2**z*360-180;east=(x+1)/2**z*360-180
                north=math.degrees(math.atan(math.sinh(math.pi*(1-2*y/2**z))))
                south=math.degrees(math.atan(math.sinh(math.pi*(1-2*(y+1)/2**z))))
                bounds=[min(bounds[0],west),min(bounds[1],south),max(bounds[2],east),max(bounds[3],north)]
    metadata={'id':layer_id,'name':name,'template':'maps/'+layer_id+'/{z}/{x}/{y}.png',
              'minzoom':min(zooms),'maxzoom':max(zooms),'bounds':bounds,
              'enabled':enabled,'transparency':35,'tile_count':len(seen),
              'tiles':sorted([list(t) for t in seen]),'source_stamp':stamps,'export_maxzoom':maxzoom}
    meta_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(name, ':',len(seen),'tiles',flush=True)
    return metadata


def main():
    (ROOT/'data').mkdir(exist_ok=True)
    samples=load_samples()
    layers=[
        export_layer('n47ix','N-47-IX · геологическая карта',[MAPS/'N-47-IX'/'N-47-IX.mbtiles'],enabled=True),
        export_layer('n47xv','N-47-XV · геологическая карта',[MAPS/'N-47-XV'/'N-47-XV.mbtiles'],enabled=True),
        export_layer('field2025','Полевой комплект 2025',[MAPS/'ГеолКарта_2025_1.sqlitedb',MAPS/'ГеолКарта_2025.sqlitedb']),
    ]
    template=(ROOT/'scripts'/'portal_template.html').read_text(encoding='utf-8')
    html=template.replace('__SAMPLES__',json.dumps(samples,ensure_ascii=False,separators=(',',':')))
    html=html.replace('__LAYERS__',json.dumps(layers,ensure_ascii=False,separators=(',',':')))
    (ROOT/'index.html').write_text(html,encoding='utf-8')
    print('Built Biryusa:',len(samples),'samples;',len(layers),'map layers')


if __name__=='__main__':main()
