import { ChangeDetectionStrategy, Component, inject, OnInit, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { NgxMapLibreGLModule } from '@maplibre/ngx-maplibre-gl';
import type { MapMouseEvent } from 'maplibre-gl';
import type { FeatureCollection, Point } from 'geojson';
import { MeetupService } from '../../services/meetup/meetup.service';
import { AddMeetupMemberRequest } from '../../open-api/model/add-meetup-member-request';

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

  readonly members = this.meetup.members;
  readonly placed = this.meetup.placed;
  readonly selected = this.meetup.selected;
  readonly selectedId = this.meetup.selectedId;

  newName = '';

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

  ngOnInit() {
    this.meetup.load();
  }

  addMember() {
    const name = this.newName.trim();
    if (!name) {
      return;
    }

    const member: AddMeetupMemberRequest = { name };
    this.meetup.addMember(member);
    this.newName = '';
  }

  selectMember(id: string) {
    this.meetup.selectMember(id);
  }

  refresh() {
    this.meetup.refresh();
  }

  onMapClick(event: MapMouseEvent) {
    const { lng, lat } = event.lngLat;

    console.log(`Clicked at lng: ${lng}, lat: ${lat}`);

    this.clickedCoords.set([lng, lat]);

    // Clicking with a member selected places (or moves) them here.
    if (this.selectedId()) {
      this.meetup.placeMember({ latitude: lat, longitude: lng });
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
