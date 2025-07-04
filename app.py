
# app.py – Générateur de leads : Google Places + surfaces via Overpass

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
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CATEGORY_KEYWORDS = {
    'Entrepôts frigorifiques': 'entrepôt frigorifique',
    'Bornes de recharge': 'station de recharge véhicule électrique',
    'Bureaux': 'bureau',
    'Fonds immobiliers': 'fonds immobilier'
}
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 41.0, 51.5, -5.5, 9.5
STEP_LAT, STEP_LON = 0.5, 0.7

# --- UTILITAIRES

def build_france_grid():
    """Génère une grille de points couvrant la France."""
    pts = []
    lat = LAT_MIN
    while lat <= LAT_MAX:
        lon = LON_MIN
        while lon <= LON_MAX:
            pts.append((lat, lon))
            lon += STEP_LON
        lat += STEP_LAT
    return pts

def estimate_api_calls(grid_pts):
    """Estime le nombre d'appels API (1 text search + 1 detail par point)."""
    return len(grid_pts) * 2

def get_building_polygon(lat, lon, radius=50):
    """Récupère les polygones OSM 'building' autour d'un point via Overpass."""
    q = f"""
    [out:json][timeout:25];
    way(around:{radius},{lat},{lon})[building];
    out body geom;
    """
    el = requests.post(OVERPASS_URL, data={'data': q}).json().get('elements', [])
    polys = []
    for e in el:
        if e['type']=='way' and 'geometry' in e:
            coords = [(pt['lon'], pt['lat']) for pt in e['geometry']]
            try:
                p = Polygon(coords)
                if p.is_valid and p.area > 0:
                    polys.append(p)
            except:
                pass
    return unary_union(polys) if polys else None

def calculate_surface_m2(poly):
    """Approxime la surface en m² d'un shapely Polygon (1°≈111km)."""
    return poly.area * (111000**2)

def search_places_text(keyword, api_key, region=None, department=None):
    """
    Recherche textuelle Google Places.
    Si department spécifié, on ajoute 'in {department}', sinon si region : 'in {region}', sinon 'in France'.
    Paginate pour récupérer jusqu'à ≈60 résultats.
    """
    gmaps = googlemaps.Client(key=api_key)
    query = keyword
    if department:
        query += f" in {department}"
    elif region:
        query += f" in {region}"
    else:
        query += " in France"
    resp = gmaps.places(query=query, language='fr')
    results = resp.get('results', [])
    while 'next_page_token' in resp:
        time.sleep(2)
        resp = gmaps.places(query=query, language='fr', page_token=resp['next_page_token'])
        results += resp.get('results', [])
    rows = []
    for p in results:
        loc = p['geometry']['location']
        rows.append({
            'place_id': p['place_id'],
            'latitude': loc['lat'],
            'longitude': loc['lng']
        })
    return pd.DataFrame(rows).drop_duplicates('place_id')

def enrich_place_details(df, api_key):
    """
    Pour chaque place_id, appelle Google Place Details (sans fields)
    puis extrait nom, téléphone, website, lien Google Maps, PagesJaunes,
    et administrative_area_level_1 & 2 pour région & département.
    """
    gmaps = googlemaps.Client(key=api_key)
    out = []
    for _, r in df.iterrows():
        try:
            detail = gmaps.place(place_id=r['place_id']).get('result', {})
        except Exception as e:
            st.error(f"Erreur Place Details ({r['place_id']}): {e}")
            continue
        region = None
        department = None
        for comp in detail.get('address_components', []):
            types = comp.get('types', [])
            if 'administrative_area_level_1' in types:
                region = comp.get('long_name')
            if 'administrative_area_level_2' in types:
                department = comp.get('long_name')
        name = detail.get('name', 'Non dispo')
        pj = f"https://www.pagesjaunes.fr/recherche/{name.replace(' ', '%20')}"
        out.append({
            'place_id': r['place_id'],
            'latitude': r['latitude'],
            'longitude': r['longitude'],
            'contact_name': name,
            'contact_phone': detail.get('international_phone_number','Non dispo'),
            'contact_website': detail.get('website','Non dispo'),
            'google_maps_link': detail.get('url','Non dispo'),
            'pagesjaunes_link': pj,
            'region': region,
            'department': department
        })
        time.sleep(1)
    return pd.DataFrame(out)

def attach_surfaces(df, min_area):
    """
    Pour chaque ligne enrichie, récupère le polygone bâtiment via Overpass,
    calcule la surface et filtre selon min_area.
    """
    recs = []
    for _, r in df.iterrows():
        poly = get_building_polygon(r['latitude'], r['longitude'])
        surf = calculate_surface_m2(poly) if poly else 0
        if surf >= min_area:
            d = r.to_dict()
            d.update({'surface_m2': surf,
                      'geometry': poly or Point(r['longitude'], r['latitude'])})
            recs.append(d)
    if not recs:
        return gpd.GeoDataFrame(columns=list(df.columns)+['surface_m2','geometry'])
    return gpd.GeoDataFrame(recs, geometry='geometry', crs='EPSG:4326')

# --- STREAMLIT APP

def main():
    # Authentification
    if 'APP_PASSWORD' not in st.secrets or 'GOOGLE_API_KEY' not in st.secrets:
        st.error("Ajoutez APP_PASSWORD et GOOGLE_API_KEY dans .streamlit/secrets.toml")
        return
    if 'pwd_ok' not in st.session_state:
        pwd = st.sidebar.text_input("Mot de passe", type="password")
        if pwd == st.secrets['APP_PASSWORD']:
            st.session_state['pwd_ok'] = True
        else:
            st.error("Mot de passe incorrect")
            return

    st.title("Générateur de leads – Places + Surfaces")

    # Sidebar : filtres
    presets = list(CATEGORY_KEYWORDS.keys())
    choice = st.sidebar.selectbox("Type de site (pré-sélection)", ["Autre"] + presets)
    if choice == "Autre":
        keyword = st.sidebar.text_input("🔎 Mot-clé Google Places", "")
    else:
        keyword = CATEGORY_KEYWORDS[choice]

    min_area = st.sidebar.number_input("Surface minimale (m²)", 0, 100000, 10000, 1000)

    st.sidebar.subheader("Filtres géographiques")
    all_regions = [  # liste manuelle ou extraite dynamiquement
        "Île-de-France", "Auvergne-Rhône-Alpes", "Nouvelle-Aquitaine",
        "Bretagne", "Occitanie", "Grand Est", "Hauts-de-France",
        "Provence-Alpes-Côte d'Azur", "Normandie", "Pays de la Loire",
        "Centre-Val de Loire", "Bourgogne-Franche-Comté", "Corse"
    ]
    all_depts = [str(i).zfill(2) for i in range(1, 96)]
    region_filter = st.sidebar.multiselect("Régions", all_regions)
    dept_filter = st.sidebar.multiselect("Départements (code)", all_depts)

    if not keyword:
        st.info("Entrez un mot-clé pour démarrer.")
        return

    # Estimation budget API
    grid_pts = build_france_grid()
    calls = estimate_api_calls(grid_pts)
    st.sidebar.subheader("💳 Budget API Places")
    st.sidebar.write(f"Appels estimés ≈ {calls}")

    if st.sidebar.button("Rechercher"):
        # Recherche Google Places selon filtres géo
        frames = []
        if dept_filter:
            for d in dept_filter:
                frames.append(search_places_text(keyword, st.secrets["GOOGLE_API_KEY"],
                                                 department=d))
        elif region_filter:
            for r in region_filter:
                frames.append(search_places_text(keyword, st.secrets["GOOGLE_API_KEY"],
                                                 region=r))
        else:
            frames.append(search_places_text(keyword, st.secrets["GOOGLE_API_KEY"]))
        df_pl = pd.concat(frames, ignore_index=True).drop_duplicates("place_id")

        # Enrichissement détails
        df_det = enrich_place_details(df_pl, st.secrets["GOOGLE_API_KEY"])

        # Surfaces & filtrage
        gdf = attach_surfaces(df_det, min_area)

        st.success(f"{len(gdf)} leads trouvés")
        st.dataframe(gdf[['region','department','surface_m2','contact_name','contact_phone']])

        # Carte Folium
        m = folium.Map(location=[46.6,2.5], zoom_start=6, tiles="cartodbpositron")
        for _, r in gdf.iterrows():
            geom = r.geometry.centroid if isinstance(r.geometry, Polygon) else r.geometry
            popup = folium.Popup(
                f"<b>{r.contact_name}</b><br>"
                f"{r.region or '–'} / {r.department or '–'}<br>"
                f"{int(r.surface_m2)} m²<br>"
                f"<a href='{r.google_maps_link}' target='_blank'>Google Maps</a>",
                max_width=300
            )
            folium.Marker([geom.y,geom.x], popup=popup).add_to(m)
        components.html(m._repr_html_(), height=500)

        # Export CSV
        df_e = gdf.copy()
        df_e['wkt'] = df_e.geometry.apply(lambda g: g.wkt)
        cols = [
            'region','department','latitude','longitude','surface_m2',
            'contact_name','contact_phone','contact_website','google_maps_link','wkt'
        ]
        st.download_button(
            "📥 Télécharger CSV",
            data=df_e[cols].to_csv(index=False),
            file_name="leads.csv",
            mime="text/csv"
        )

if __name__ == "__main__":
    main()


