# app.py - Générateur de leads : Google Places + surfaces via Overpass

import json
import time
import requests
import pandas as pd
import geopandas as gpd
import googlemaps
import folium
import streamlit as st
import streamlit.components.v1 as components
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

# --- CONSTANTES
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 41.0, 51.5, -5.5, 9.5
STEP_LAT, STEP_LON = 0.5, 0.7
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
GOOGLE_FIELDS = [
    'name', 'formatted_address', 'international_phone_number',
    'website', 'url', 'address_components'
]

# --- UTILS

def build_france_grid():
    """Génère une grille de points couvrant la France"""
    points = []
    lat = LAT_MIN
    while lat <= LAT_MAX:
        lon = LON_MIN
        while lon <= LON_MAX:
            points.append((lat, lon))
            lon += STEP_LON
        lat += STEP_LAT
    return points

def estimate_api_calls(grid_points, include_details=True):
    """Estime le nombre d'appels API (1 search + optionnellement 1 détail par point)"""
    return len(grid_points) * (1 + int(include_details))

def get_building_polygon(lat, lon, radius=50):
    """
    Interroge Overpass pour obtenir les polygones 'building' autour d'un point.
    Retourne un shapely Polygon (union si plusieurs) ou None.
    """
    query = f"""
    [out:json][timeout:25];
    way(around:{radius},{lat},{lon})[building];
    out body geom;
    """
    resp = requests.post(OVERPASS_URL, data={'data': query})
    data = resp.json().get('elements', [])
    polys = []
    for elem in data:
        if elem['type'] == 'way' and 'geometry' in elem:
            coords = [(pt['lon'], pt['lat']) for pt in elem['geometry']]
            try:
                poly = Polygon(coords)
                if poly.is_valid and poly.area > 0:
                    polys.append(poly)
            except Exception:
                continue
    if not polys:
        return None
    return unary_union(polys)

def calculate_surface_m2(poly):
    """
    Calcule la surface en m² d'un shapely Polygon via projection approximative.
    """
    return poly.area * (111000 ** 2)

def search_places(keyword, api_key, grid):
    """Recherche Google Places (Text Search) sur chaque point de la grille."""
    gmaps = googlemaps.Client(key=api_key)
    recs = []
    for lat, lon in grid:
        try:
            res = gmaps.places(query=keyword, location=(lat, lon),
                               radius=25000, region='fr')
        except Exception as e:
            st.error(f"Erreur API Places: {e}")
            return pd.DataFrame()
        for p in res.get('results', []):
            recs.append({
                'place_id': p['place_id'],
                'latitude': p['geometry']['location']['lat'],
                'longitude': p['geometry']['location']['lng']
            })
        time.sleep(1)
    return pd.DataFrame(recs).drop_duplicates('place_id')

def enrich_place_details(df, api_key):
    """Enrichit chaque place_id avec Place Details et extrait région/département."""
    gmaps = googlemaps.Client(key=api_key)
    out = []
    for _, r in df.iterrows():
        try:
            detail = gmaps.place(place_id=r['place_id'], fields=GOOGLE_FIELDS).get('result', {})
        except Exception as e:
            st.error(f"Erreur Place Details: {e}")
            return pd.DataFrame()
        region = None
        department = None
        for comp in detail.get('address_components', []):
            types = comp.get('types', [])
            if 'administrative_area_level_1' in types:
                region = comp.get('long_name')
            if 'administrative_area_level_2' in types:
                department = comp.get('long_name')
        name = detail.get('name', 'Non dispo')
        pagesjaunes = f"https://www.pagesjaunes.fr/recherche/{name.replace(' ', '%20')}"
        out.append({
            'place_id': r['place_id'],
            'latitude': r['latitude'],
            'longitude': r['longitude'],
            'contact_name': name,
            'contact_phone': detail.get('international_phone_number', 'Non dispo'),
            'contact_website': detail.get('website', 'Non dispo'),
            'google_maps_link': detail.get('url', 'Non dispo'),
            'pagesjaunes_link': pagesjaunes,
            'region': region,
            'department': department
        })
        time.sleep(1)
    return pd.DataFrame(out)

def attach_surfaces(df, min_area):
    """Calcule la surface du bâtiment via Overpass, filtre selon min_area."""
    recs = []
    for _, r in df.iterrows():
        poly = get_building_polygon(r['latitude'], r['longitude'])
        surf = calculate_surface_m2(poly) if poly else 0
        if surf >= min_area:
            rec = r.to_dict()
            rec.update({'surface_m2': surf,
                        'geometry': poly or Point(r['longitude'], r['latitude'])})
            recs.append(rec)
    if not recs:
        return gpd.GeoDataFrame(columns=list(df.columns) + ['surface_m2', 'geometry'])
    gdf = gpd.GeoDataFrame(recs, geometry='geometry', crs='EPSG:4326')
    return gdf

# --- INTERFACE STREAMLIT

def main():
    # Authentification
    if 'APP_PASSWORD' not in st.secrets or 'GOOGLE_API_KEY' not in st.secrets:
        st.error('Configurez APP_PASSWORD et GOOGLE_API_KEY dans .streamlit/secrets.toml')
        return
    if 'password_correct' not in st.session_state:
        pwd = st.sidebar.text_input('Mot de passe', type='password')
        if pwd == st.secrets['APP_PASSWORD']:
            st.session_state['password_correct'] = True
        else:
            st.error('Mot de passe incorrect')
            return

    st.title('Générateur de leads – Google Places + Surfaces')

    # Filtres
    presets = ['Entrepôts frigorifiques', 'Bornes de recharge', 'Bureaux']
    choice = st.sidebar.selectbox('Type de site (pré-sélection)', ['Autre'] + presets)
    keyword = choice if choice != 'Autre' else st.sidebar.text_input('🔎 Mot-clé Google Places', '')
    min_area = st.sidebar.number_input('Surface minimale (m²)', 0, 100000, 10000, 1000)

    st.sidebar.subheader('Filtres géographiques')
    region_filter = st.sidebar.multiselect('Régions', [])
    dept_filter = st.sidebar.multiselect('Départements', [])

    if not keyword:
        st.info('Entrez un mot-clé pour démarrer.')
        return

    # Estimation d'appels
    grid_pts = build_france_grid()
    n_calls = estimate_api_calls(grid_pts)
    st.sidebar.subheader('💳 Budget API Places')
    max_calls = st.sidebar.number_input('Seuil max d’appels API', 0, 5000, 1000, 100)
    st.sidebar.write(f'Appels estimés : **{n_calls}**')
    disabled = n_calls > max_calls
    if disabled:
        st.sidebar.error('🔴 Trop d’appels API, ajustez le seuil ou la grille.')

    # Lancer la recherche
    if st.sidebar.button('Rechercher', disabled=disabled):
        df_pl = search_places(keyword, st.secrets['GOOGLE_API_KEY'], grid_pts)
        df_det = enrich_place_details(df_pl, st.secrets['GOOGLE_API_KEY'])
        gdf = attach_surfaces(df_det, min_area)

        # Mise à jour dynamique des filtres géo
        regions = sorted(gdf['region'].dropna().unique())
        departments = sorted(gdf['department'].dropna().unique())
        region_filter = st.sidebar.multiselect('Régions', regions, region_filter)
        dept_filter = st.sidebar.multiselect('Départements', departments, dept_filter)

        if region_filter:
            gdf = gdf[gdf['region'].isin(region_filter)]
        if dept_filter:
            gdf = gdf[gdf['department'].isin(dept_filter)]

        st.success(f'{len(gdf)} leads trouvés')
        st.dataframe(gdf[['region','department','surface_m2','contact_name','contact_phone']])

        # Carte Folium
        m = folium.Map(location=[46.6,2.5], zoom_start=6, tiles='cartodbpositron')
        for _, r in gdf.iterrows():
            geom = r.geometry.centroid if isinstance(r.geometry, Polygon) else r.geometry
            popup = folium.Popup(
                f"<b>{r.contact_name}</b><br>"
                f"{r.region} / {r.department}<br>"
                f"Surface : {int(r.surface_m2)} m²<br>"
                f"<a href='{r.google_maps_link}' target='_blank'>Google Maps</a>",
                max_width=300
            )
            folium.Marker([geom.y, geom.x], popup=popup).add_to(m)
        components.html(m._repr_html_(), height=500)

        # Export CSV
        df_e = gdf.copy()
        df_e['wkt'] = df_e.geometry.apply(lambda g: g.wkt)
        cols = ['region','department','latitude','longitude','surface_m2',
                'contact_name','contact_phone','contact_website','google_maps_link','wkt']
        st.download_button(
            '📥 Télécharger CSV',
            data=df_e[cols].to_csv(index=False),
            file_name='leads.csv',
            mime='text/csv'
        )

if __name__ == '__main__':
    main()

