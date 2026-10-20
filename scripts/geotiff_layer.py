"""Export north-up EPSG:4326 GeoTIFFs as Web Mercator XYZ PNG tiles.

Only Pillow is required. Each 16-pixel mesh strip transforms Web Mercator
latitude back to source pixels; longitude is linear. Source rasters are read-only.
"""
import json
import math
from PIL import Image


def export_geotiff(root, source_root, path, minzoom=8, maxzoom=14):
    layer_id = 'biryusa2025'
    layer_name = 'N-47-XV_3g'
    output = root / 'maps' / layer_id
    stamp = [{'file':str(path.relative_to(source_root)), 'size':path.stat().st_size,
              'mtime_ns':path.stat().st_mtime_ns}]
    metadata_path = output / 'metadata.json'
    if metadata_path.exists():
        previous = json.loads(metadata_path.read_text(encoding='utf-8'))
        if (previous.get('source_stamp') == stamp
                and previous.get('export_maxzoom') == maxzoom
                and previous.get('minzoom') == minzoom):
            previous['name'] = layer_name
            return previous
    with Image.open(path) as original:
        tags = original.tag_v2
        keys = tags.get(34735, ())
        geokeys = {keys[i]: keys[i+3] for i in range(4, len(keys), 4)
                   if keys[i+1] == 0 and keys[i+2] == 1}
        if geokeys.get(2048) != 4326 or geokeys.get(1025) != 1 or 34264 in tags:
            raise ValueError('Expected north-up EPSG:4326 PixelIsArea GeoTIFF')
        sx, sy, _ = tags[33550]
        px, py, _, lon, lat, _ = tags[33922]
        if sx <= 0 or sy <= 0:
            raise ValueError('Invalid pixel scale')
        west, north = lon - px*sx, lat + py*sy
        width, height = original.size
        east, south = west + width*sx, north - height*sy
        image = original.convert('RGBA')

    def tile_x(lon, z):
        return (lon+180)/360 * 2**z

    def tile_y(lat, z):
        return (1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2 * 2**z

    def source_y(tile_row, z):
        latitude = math.degrees(math.atan(math.sinh(math.pi*(1-2*tile_row/2**z))))
        return (north-latitude)/sy

    output.mkdir(parents=True, exist_ok=True)
    tiles = []
    for z in range(minzoom, maxzoom+1):
        x0, x1 = math.floor(tile_x(west,z)), math.ceil(tile_x(east,z))-1
        y0, y1 = math.floor(tile_y(north,z)), math.ceil(tile_y(south,z))-1
        for y in range(y0, y1+1):
            rows = [(v, source_y(y+v/256,z)) for v in range(0,257,16)]
            for x in range(x0, x1+1):
                left = (x/2**z*360-180-west)/sx
                right = ((x+1)/2**z*360-180-west)/sx
                mesh = [((0,v0,256,v1), (left,s0,left,s1,right,s1,right,s0))
                        for (v0,s0),(v1,s1) in zip(rows,rows[1:])]
                tile = image.transform((256,256), Image.Transform.MESH, mesh,
                                       Image.Resampling.BICUBIC, fillcolor=(0,0,0,0))
                if tile.getchannel('A').getbbox() is None:
                    continue
                target = output/str(z)/str(x)/(str(y)+'.png')
                target.parent.mkdir(parents=True, exist_ok=True)
                tile.save(target, compress_level=6)
                tiles.append([z,x,y])
        print(layer_name+': zoom',z,'—',len(tiles),'tiles total',flush=True)
    metadata = {'id':layer_id,'name':layer_name,
                'template':'maps/'+layer_id+'/{z}/{x}/{y}.png',
                'minzoom':minzoom,'maxzoom':maxzoom,'bounds':[west,south,east,north],
                'enabled':True,'transparency':35,'tile_count':len(tiles),
                'tiles':sorted(tiles),'source_stamp':stamp,'export_maxzoom':maxzoom,
                'source_crs':'EPSG:4326','tile_crs':'EPSG:3857',
                'source_size':[width,height],'source_pixel_scale':[sx,sy],
                'georeferencing':'Embedded GeoTIFF tiepoint and pixel scale; no manual control points'}
    metadata_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    return metadata
