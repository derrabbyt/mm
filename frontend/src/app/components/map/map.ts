import { ChangeDetectionStrategy, Component, inject, OnInit, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { NgxMapLibreGLModule } from '@maplibre/ngx-maplibre-gl';
import type { MapMouseEvent } from 'maplibre-gl';
import type { FeatureCollection, Point } from 'geojson';
import { MeetupService } from '../../services/meetup/meetup.service';

@Component({
  selector: 'app-map',
  standalone: true,
  imports: [NgxMapLibreGLModule, DecimalPipe, FormsModule],
  templateUrl: './map.html',
  styleUrl: './map.css',
  changeDetection: ChangeDetectionStrategy.OnPush,

})
export class Map implements OnInit {

  private meetup = inject(MeetupService);

  readonly participants = this.meetup.participants;
  readonly placed = this.meetup.placed;
  readonly selected = this.meetup.selected;
  readonly selectedId = this.meetup.selectedId;
  readonly activeMeetup = this.meetup.activeMeetup;

  newName = '';

  readonly mapStyle = 'https://tiles.openfreemap.org/styles/liberty';

  readonly center: [number, number] = [16.3738, 48.2082];

  readonly zoom: [number] = [12];

  private static readonly POINT_COUNT = 20;

  private static readonly SPREAD_DEG = 0.05;

  readonly points = signal<FeatureCollection<Point>>({
    type: 'FeatureCollection',
    features: [],
  });

  readonly clickedCoords = signal<[number, number] | null>(null);

  ngOnInit() {
    this.meetup.load();
  }

  addParticipant() {
    const name = this.newName.trim();
    if (!name) {
      return;
    }

    this.meetup.addParticipant(name);
    this.newName = '';
  }

  selectParticipant(id: string) {
    this.meetup.selectParticipant(id);
  }

  refresh() {
    this.meetup.refresh();
  }

  onMapClick(event: MapMouseEvent) {
    const { lng, lat } = event.lngLat;

    console.log(`Clicked at lng: ${lng}, lat: ${lat}`);

    this.clickedCoords.set([lng, lat]);

    if (this.selectedId()) {
      this.meetup.placeParticipant({ latitude: lat, longitude: lng });
    }
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
