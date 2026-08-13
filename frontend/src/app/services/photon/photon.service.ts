import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { map, Observable } from 'rxjs';
import { Position } from '../../open-api/model/position';
import { environment } from '../../../environments/environment';

/** Raw Photon feature properties. Every field is optional - a reverse lookup on
 *  a building returns street/housenumber and no name, a place search returns a
 *  name and often no housenumber. */
export interface PhotonProperties {
  osm_id?: number;
  osm_type?: string;
  osm_key?: string;
  osm_value?: string;
  name?: string;
  street?: string;
  housenumber?: string;
  postcode?: string;
  city?: string;
  district?: string;
  locality?: string;
  state?: string;
  country?: string;
  countrycode?: string;
}

interface PhotonFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: PhotonProperties;
}

interface PhotonFeatureCollection {
  type: 'FeatureCollection';
  features: PhotonFeature[];
}

/** A Photon hit, normalised so callers never touch GeoJSON's [lon, lat] order. */
export interface PhotonPlace {
  /** Stable-ish key for template tracking; Photon has no id of its own. */
  key: string;
  /** One line fit for a dropdown row, e.g. "Stephansplatz 4". */
  label: string;
  /** The administrative tail, e.g. "1010 Wien, Osterreich". */
  context: string;
  position: Position;
  properties: PhotonProperties;
}

export interface SearchOptions {
  /** Biases results towards this point - Photon ranks by distance from it. */
  near?: Position | null;
  limit?: number;
  lang?: string;
}

function buildLabel(properties: PhotonProperties): string {
  const street = [properties.street, properties.housenumber].filter(Boolean).join(' ');

  // A named POI leads with its name and keeps the street as a qualifier;
  // a plain address has no name and leads with the street itself.
  if (properties.name && street && properties.name !== properties.street) {
    return `${properties.name}, ${street}`;
  }
  return properties.name ?? street ?? properties.city ?? 'Unnamed place';
}

function buildContext(properties: PhotonProperties): string {
  const locality = [properties.postcode, properties.city ?? properties.district]
    .filter(Boolean)
    .join(' ');
  return [locality, properties.state, properties.country].filter(Boolean).join(', ');
}

function toPlace(feature: PhotonFeature, index: number): PhotonPlace {
  const [longitude, latitude] = feature.geometry.coordinates;
  const properties = feature.properties;
  return {
    key: `${properties.osm_type ?? '?'}${properties.osm_id ?? index}-${index}`,
    label: buildLabel(properties),
    context: buildContext(properties),
    position: { latitude, longitude },
    properties,
  };
}

@Injectable({ providedIn: 'root' })
export class PhotonService {
  private http = inject(HttpClient);
  private baseUrl = environment.photonBaseUrl.replace(/\/$/, '');

  search(query: string, options: SearchOptions = {}): Observable<PhotonPlace[]> {
    let params = new HttpParams()
      .set('q', query)
      .set('limit', options.limit ?? 5)
      .set('lang', options.lang ?? 'en');

    if (options.near) {
      params = params.set('lat', options.near.latitude).set('lon', options.near.longitude);
    }

    return this.http
      .get<PhotonFeatureCollection>(`${this.baseUrl}/api/`, { params })
      .pipe(map((collection) => collection.features.map(toPlace)));
  }

  /** Nearest addressable thing to a coordinate, or null if Photon knows none. */
  reverse(
    position: Position,
    options: Omit<SearchOptions, 'near'> = {},
  ): Observable<PhotonPlace | null> {
    const params = new HttpParams()
      .set('lat', position.latitude)
      .set('lon', position.longitude)
      .set('limit', options.limit ?? 1)
      .set('lang', options.lang ?? 'en');

    return this.http
      .get<PhotonFeatureCollection>(`${this.baseUrl}/reverse`, { params })
      .pipe(
        map((collection) => (collection.features[0] ? toPlace(collection.features[0], 0) : null)),
      );
  }
}
