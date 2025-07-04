```python
# app.py - Générateur de leads : Google Places + surfaces via Overpass

import json
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import googlemaps
import folium
import time
import requests
from shapely.geometry import shape, Point, Polygon
from shapely.ops import unary_union

# --- CONSTANTES
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 41.0, 51.5, -5.5, 9.5
STEP_LAT, STEP_LON = 0.5, 0.7
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
GOOGLE_FIELDS = ['name','formatted_address','international_phone_number','website','url']

# --- UTILS

def build_france_grid():
    points = []
    lat = LAT_MIN
    while lat <= LAT_MAX:
        lon = LON_MIN
        while lon <= LON_MAX:
            points.append((lat, lon))
            lon += STEP_LON
        lat += STEP_LAT
    return points


def get_building_polygon(lat, lon, radius=50):
    """
    Interroge Overpass pour obtenir les polygones bâtis autour d'un point.
    Récupère les ways tagged 'building' dans un cercle de `radius` mètres.
    Retourne un shapely Polygon (union si plusieurs) ou None.
    """
    # Overpass QL: way(around:radius,lat,lon)[building];->;out body;
    query = f"""
    [out:json][timeout:25];
    way(around:{radius},{lat},{lon})[building];
    out body geom;
    """
    resp = requests.post(OVERPASS_URL, data={'data': query})
    data = resp.json().get('elements', [])
    polys = []
    for elem in data:
        if elem['type']=='way' and 'geometry' in elem:
            coords = [(pt['lon'], pt['lat']) for pt in elem['geometry']]
            try:
                poly = Polygon(coords)
                if poly.is_valid and poly.area>0:
                    polys.append(poly)
            except Exception:
                continue
    if not polys:
        return None
    # fusionne tous
    return unary_union(polys)


def calculate_surface_m2(poly):
    """
    Calcule la surface en m² d'un shapely Polygon en projetant approximativement via factor.
    (On peut améliorer via pyproj, mais ici approx: deg→m conversion ~111km)
    """
    # Simple approximation: transform degrees to meters (latitude scale)
    # For plus de précision, utiliser pyproj. Ici: rough.
    # Convert polygon to a local UTM projection omitted for brevity.
    return poly.area * (111000**2) / ((360/ (2*3.14159))**2)

# --- FONCTIONS PRINCIPALES

def search_places(keyword, api_key, grid):
    """Récupère place_id et coords via Google Places Text Search sur la grille."""
    gmaps = googlemaps.Client(key=api_key)
    recs=[]
    for lat, lon in grid:
        res=gmaps.places(query=keyword, location=(lat,lon), radius=25000, region='fr')
        for p in res.get('results',[]):
            recs.append({'place_id': p['place_id'], 'latitude':p['geometry']['location']['lat'], 'longitude':p['geometry']['location']['lng']})
        time.sleep(1)
    return pd.DataFrame(recs).drop_duplicates('place_id')


def enrich_place_details(df, api_key):
    """Récupère nom, téléphone, site, lien Google Maps pour chaque place_id."""
    gmaps = googlemaps.Client(key=api_key)
    out=[]
    for _, r in df.iterrows():
        det=gmaps.place(place_id=r['place_id'], fields=GOOGLE_FIELDS).get('result',{})
        out.append({
            'place_id': r['place_id'],
            'latitude': r['latitude'],
            'longitude': r['longitude'],
            'contact_name': det.get('name','Non dispo'),
            'contact_phone': det.get('international_phone_number','Non dispo'),
            'contact_website': det.get('website','Non dispo'),
            'google_maps_link': det.get('url','Non dispo'),
        })
        time.sleep(1)
    return pd.DataFrame(out)


def attach_surfaces(df, min_area):
    """Pour chaque ligne, récupère le polygone building via Overpass, calcule surface, filtre."""
    recs=[]
    for _, r in df.iterrows():
        poly = get_building_polygon(r['latitude'], r['longitude'])
        surf=calculate_surface_m2(poly) if poly else 0
        if surf>=min_area:
            recs.append({**r.to_dict(), 'surface_m2': surf, 'geometry': poly or Point(r['longitude'],r['latitude'])})
    gdf = gpd.GeoDataFrame(recs, geometry='geometry', crs='EPSG:4326')
    return gdf

# --- INTERFACE STREAMLIT

def main():
    # Secrets
    if 'APP_PASSWORD' not in st.secrets or 'GOOGLE_API_KEY' not in st.secrets:
        st.error('Configurez APP_PASSWORD et GOOGLE_API_KEY dans secrets.toml')
        return
    # Auth
    if 'password_correct' not in st.session_state:
        pwd=st.sidebar.text_input('Mot de passe', type='password')
        if pwd==st.secrets['APP_PASSWORD']:
            st.session_state['password_correct']=True
        else:
            st.error('Mot de passe incorrect')
            return
    st.title('Générateur de leads: Places + Surfaces')
    # Filtres
    presets=['Entrepôts frigorifiques','Bornes de recharge','Bureaux']
    choice=st.sidebar.selectbox('Type de site', ['Autre']+presets)
    keyword=choice if choice!='Autre' else st.sidebar.text_input('Mot-clé', '')
    min_area=st.sidebar.number_input('Surface min (m²)',0,100000,10000,1000)
    if not keyword:
        st.info('Entrez un mot-clé')
        return
    # Recherche & enrichissement
    grid=build_france_grid()
    df_pl=search_places(keyword, st.secrets['GOOGLE_API_KEY'], grid)
    df_det=enrich_place_details(df_pl, st.secrets['GOOGLE_API_KEY'])
    # Surfaces via Overpass
    gdf=attach_surfaces(df_det, min_area)
    st.success(f'{len(gdf)} leads trouvés')
    # Dataframe
    st.dataframe(gdf[['surface_m2','contact_name','contact_phone','contact_website']])
    # Carte
    m=folium.Map(location=[46.6,2.5],zoom_start=6,tiles='CartoDB positron')
    for _,r in gdf.iterrows():
        geom=r.geometry.centroid if isinstance(r.geometry, Polygon) else r.geometry
        folium.Marker([geom.y,geom.x], popup=r.contact_name).add_to(m)
    html=m._repr_html_()
    components.html(html,height=500)
    # Export CSV
    df_export=gdf.copy()
    df_export['wkt']=df_export.geometry.apply(lambda g: g.wkt)
    csv=df_export[['latitude','longitude','surface_m2','contact_name','contact_phone','contact_website','google_maps_link','wkt']].to_csv(index=False)
    st.download_button('Télécharger CSV', data=csv, file_name='leads.csv', mime='text/csv')

if __name__=='__main__':
    main()
```
