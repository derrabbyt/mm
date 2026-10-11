import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { map, Observable } from 'rxjs';
import { Position } from '../../open-api/model/position';
import { environment } from '../../../environments/environment';

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

export interface PhotonPlace {
  key: string;
  label: string;
  context: string;
  position: Position;
  properties: PhotonProperties;
}

export interface SearchOptions {
  near?: Position | null;
  limit?: number;
  lang?: string;
}

function buildLabel(properties: PhotonProperties): string {
  const street = [properties.street, properties.housenumber].filter(Boolean).join(' ');

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
