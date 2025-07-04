# app.py – Générateur de leads : Google Places + Surfaces via Overpass

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
GOOGLE_FIELDS = [
    'name','formatted_address','international_phone_number',
    'website','url','address_components'
]
CATEGORY_KEYWORDS = {
    'Entrepôts frigorifiques':        'entrepôt frigorifique',
    'Bornes de recharge':             'station de recharge véhicule électrique',
    'Bureaux':                        'bureau',
    'Fonds immobiliers':              'fonds immobilier'
}

# --- UTILS

def get_building_polygon(lat, lon, radius=50):
    """Récupère polygones OSM 'building' autour d'un point via Overpass."""
    q = f"""
    [out:json][timeout:25];
    way(around:{radius},{lat},{lon})[building];
    out body geom;
    """
    r = requests.post(OVERPASS_URL, data={'data': q}).json().get('elements',[])
    polys=[]
    for e in r:
        if e['type']=='way' and 'geometry' in e:
            coords=[(pt['lon'],pt['lat']) for pt in e['geometry']]
            try:
                p=Polygon(coords)
                if p.is_valid and p.area>0: polys.append(p)
            except: pass
    return unary_union(polys) if polys else None

def calculate_surface_m2(poly):
    """Approxime la surface en m² d'un shapely Polygon (1°≈111km)."""
    return poly.area*(111000**2)

def search_places_text(keyword, api_key):
    """
    Recherche textuelle Google Places en France, récupère tous résultats (≤60).
    """
    gmaps = googlemaps.Client(key=api_key)
    recs=[]
    resp = gmaps.places(query=f"{keyword} in France", language='fr')
    recs += resp.get('results', [])
    # pagination jusqu'à 60 résultats max
    while 'next_page_token' in resp:
        time.sleep(2)
        resp = gmaps.places(query=f"{keyword} in France",
                            language='fr',
                            page_token=resp['next_page_token'])
        recs += resp.get('results', [])
    # on construit DataFrame minimal
    rows=[]
    for p in recs:
        loc=p['geometry']['location']
        rows.append({
            'place_id': p['place_id'],
            'latitude': loc['lat'],
            'longitude': loc['lng']
        })
    return pd.DataFrame(rows).drop_duplicates('place_id')

def enrich_place_details(df, api_key):
    """
    Pour chaque place_id, appelle Google Place Details, récupère contact + géo,
    extrait region & department depuis address_components.
    """
    gmaps = googlemaps.Client(key=api_key)
    out=[]
    for _,r in df.iterrows():
        det = gmaps.place(place_id=r['place_id'], fields=GOOGLE_FIELDS).get('result',{})
        # extraction géo
        region=None; dept=None
        for comp in det.get('address_components',[]):
            types=comp.get('types',[])
            if 'administrative_area_level_1' in types: region=comp.get('long_name')
            if 'administrative_area_level_2' in types: dept=comp.get('long_name')
        name=det.get('name','Non dispo')
        pagesjaunes=f"https://www.pagesjaunes.fr/recherche/{name.replace(' ','%20')}"
        out.append({
            'place_id': r['place_id'],
            'latitude': r['latitude'],
            'longitude': r['longitude'],
            'contact_name': name,
            'contact_phone': det.get('international_phone_number','Non dispo'),
            'contact_website': det.get('website','Non dispo'),
            'google_maps_link': det.get('url','Non dispo'),
            'pagesjaunes_link': pagesjaunes,
            'region': region,
            'department': dept
        })
        time.sleep(1)
    return pd.DataFrame(out)

def attach_surfaces(df, min_area):
    """Pour chaque lieu, récupère polygone via Overpass, calcule surface et filtre."""
    recs=[]
    for _,r in df.iterrows():
        poly=get_building_polygon(r['latitude'],r['longitude'])
        surf=calculate_surface_m2(poly) if poly else 0
        if surf>=min_area:
            d=r.to_dict()
            d.update({'surface_m2':surf,'geometry':poly or Point(r['longitude'],r['latitude'])})
            recs.append(d)
    if not recs:
        return gpd.GeoDataFrame(columns=list(df.columns)+['surface_m2','geometry'])
    gdf=gpd.GeoDataFrame(recs,geometry='geometry',crs='EPSG:4326')
    return gdf

# --- STREAMLIT APP

def main():
    # 1. Authentification
    if 'APP_PASSWORD' not in st.secrets or 'GOOGLE_API_KEY' not in st.secrets:
        st.error("Ajoutez APP_PASSWORD et GOOGLE_API_KEY dans `.streamlit/secrets.toml`")
        return
    if 'pwd_ok' not in st.session_state:
        pwd=st.sidebar.text_input("Mot de passe", type="password")
        if pwd==st.secrets["APP_PASSWORD"]:
            st.session_state['pwd_ok']=True
        else:
            st.error("Mot de passe incorrect")
            return

    st.title("Générateur de leads – Places + Surfaces")

    # 2. Filtres
    presets=list(CATEGORY_KEYWORDS.keys())
    choice=st.sidebar.selectbox("Type de site (pré-sélection)", ["Autre"]+presets)
    if choice=="Autre":
        keyword=st.sidebar.text_input("🔎 Mot-clé Google Places","")
    else:
        keyword=CATEGORY_KEYWORDS[choice]

    min_area=st.sidebar.number_input("Surface min (m²)", 0, 100000, 10000, 1000)

    # Budget appels
    est_calls=None
    if keyword:
        est_calls=1+1  # 1 text search + 1 détail par lieu (approx)
        st.sidebar.write(f"Appels Google estimés ≈ {est_calls}")

    # Région / Département filtres
    region_filter=[]; dept_filter=[]

    if not keyword:
        st.info("Entrez un mot-clé pour lancer la recherche.")
        return

    if st.sidebar.button("Rechercher"):
        # 3. Recherche & enrich
        with st.spinner("Recherche Google Places..."):
            df_pl=search_places_text(keyword, st.secrets["GOOGLE_API_KEY"])
        if df_pl.empty:
            st.warning("Aucun résultat Google.")
            return
        with st.spinner("Enrichissement des détails..."):
            df_det=enrich_place_details(df_pl, st.secrets["GOOGLE_API_KEY"])
        # 4. Surfaces & filtres
        with st.spinner("Récupération surfaces OSM..."):
            gdf=attach_surfaces(df_det, min_area)
        # remplir listes dynamiques
        regions=sorted(gdf['region'].dropna().unique())
        departments=sorted(gdf['department'].dropna().unique())
        region_filter=st.sidebar.multiselect("Régions", regions)
        dept_filter=st.sidebar.multiselect("Départements", departments)
        if region_filter:
            gdf=gdf[gdf['region'].isin(region_filter)]
        if dept_filter:
            gdf=gdf[gdf['department'].isin(dept_filter)]

        st.success(f"{len(gdf)} leads trouvés")
        st.dataframe(gdf[['region','department','surface_m2','contact_name','contact_phone']])

        # Carte
        m=folium.Map(location=[46.6,2.5],zoom_start=6,tiles="cartodbpositron")
        for _,r in gdf.iterrows():
            geom=r.geometry.centroid if isinstance(r.geometry,Polygon) else r.geometry
            popup=f"<b>{r.contact_name}</b><br>{r.region} / {r.department}<br>"\
                  f"{int(r.surface_m2)} m²<br>"\
                  f"<a href='{r.google_maps_link}' target='_blank'>Google Maps</a>"
            folium.Marker([geom.y,geom.x],popup=popup).add_to(m)
        components.html(m._repr_html_(),height=500)

        # Export CSV
        df_e=gdf.copy()
        df_e['wkt']=df_e.geometry.apply(lambda g: g.wkt)
        cols=['region','department','latitude','longitude',
              'surface_m2','contact_name','contact_phone',
              'contact_website','google_maps_link','wkt']
        st.download_button(
            "📥 Télécharger CSV",
            data=df_e[cols].to_csv(index=False),
            file_name="leads.csv",
            mime="text/csv"
        )

if __name__=="__main__":
    main()
