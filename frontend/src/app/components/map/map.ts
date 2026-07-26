import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { NgxMapLibreGLModule } from '@maplibre/ngx-maplibre-gl';
import type { MapMouseEvent } from 'maplibre-gl';
import type { FeatureCollection, Point } from 'geojson';

@Component({
  selector: 'app-map',
  standalone: true,
  imports: [NgxMapLibreGLModule, DecimalPipe],
  templateUrl: './map.html',
  styleUrl: './map.css',
  changeDetection: ChangeDetectionStrategy.OnPush,

})
export class Map {

  readonly mapStyle = 'https://tiles.openfreemap.org/styles/liberty';

  // IMPORTANT: MapLibre coordinates are [longitude, latitude]
  readonly center: [number, number] = [16.3738, 48.2082];

  readonly zoom: [number] = [12];

  private static readonly POINT_COUNT = 20;

  // Half-extent of the box the points are scattered in, in degrees around
  // `center`. Roughly ~5km at Vienna's latitude, so points stay in view at zoom 12.
  private static readonly SPREAD_DEG = 0.05;

  readonly points = signal<FeatureCollection<Point>>({
    type: 'FeatureCollection',
    features: [],
  });

  /** Where the user last clicked, or null before the first click. */
  readonly clickedCoords = signal<[number, number] | null>(null);

  onMapClick(event: MapMouseEvent) {
    const { lng, lat } = event.lngLat;

    console.log(`Clicked at lng: ${lng}, lat: ${lat}`);

    this.clickedCoords.set([lng, lat]);
  }

  start() {
    console.log('Starting to display random points on the map...');

    const [centerLng, centerLat] = this.center;
    const spread = Map.SPREAD_DEG;

    const features = Array.from({ length: Map.POINT_COUNT }, (_, i) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [
          centerLng + (Math.random() * 2 - 1) * spread,
          centerLat + (Math.random() * 2 - 1) * spread,
        ] as [number, number],
      },
      properties: { id: i },
    }));

    this.points.set({ type: 'FeatureCollection', features });
  }
}
