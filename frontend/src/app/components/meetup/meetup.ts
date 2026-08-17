import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  effect,
  signal,
} from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { NgxMapLibreGLModule } from '@maplibre/ngx-maplibre-gl';
import type { MapMouseEvent } from 'maplibre-gl';
import { MeetupService } from '../../services/meetup/meetup.service';
import {
  ParticipantEdit,
  ParticipantItem,
  ParticipantLocation,
} from './participant-item/participant-item';
import { Position } from '../../open-api/model/position';
import type { Feature, Polygon } from 'geojson';

const EARTH_RADIUS_METERS = 6_371_008.8;

/**
 * A circle of `radiusMeters` around `center`, as a polygon so it stays the
 * right size on the ground at every zoom - a `circle` layer would be sized in
 * screen pixels instead.
 *
 * The offsets are flat-earth rather than great-circle, which is off by
 * centimetres at the kilometre scale this draws and keeps the maths readable.
 */
function circle(center: Position, radiusMeters: number, steps = 64): Feature<Polygon> {
  const degreesLat = (radiusMeters / EARTH_RADIUS_METERS) * (180 / Math.PI);
  const degreesLon = degreesLat / Math.cos((center.latitude * Math.PI) / 180);

  const ring = Array.from({ length: steps + 1 }, (_, step) => {
    const angle = (step / steps) * 2 * Math.PI;
    return [
      center.longitude + degreesLon * Math.cos(angle),
      center.latitude + degreesLat * Math.sin(angle),
    ];
  });

  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } };
}

@Component({
  selector: 'app-meetup',
  imports: [NgxMapLibreGLModule, FormsModule, DatePipe, DecimalPipe, ParticipantItem],
  templateUrl: './meetup.html',
  styleUrl: './meetup.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Meetup {
  private meetups = inject(MeetupService);

  readonly id = input.required<string>();

  readonly meetup = this.meetups.meetup;
  readonly participants = this.meetups.participants;
  readonly placed = this.meetups.placed;
  readonly selectedId = this.meetups.selectedId;
  readonly rendezvous = this.meetups.rendezvous;
  readonly rendezvousError = this.meetups.rendezvousError;
  readonly travelTimes = this.meetups.travelTimes;
  readonly excludedNames = this.meetups.excludedNames;
  readonly addressLabels = this.meetups.addressLabels;
  readonly events = this.meetups.events;
  readonly eventRadiusMeters = this.meetups.eventRadiusMeters;

  /** The area the events were searched in, drawn around the rendezvous point. */
  readonly searchArea = computed(() => {
    const spot = this.rendezvous();
    return spot ? circle(spot.position, this.eventRadiusMeters) : null;
  });

  readonly newName = signal('');

  readonly worstMinutes = computed(() => {
    const spot = this.rendezvous();
    return spot ? Math.round(spot.worst_seconds / 60) : null;
  });

  /** Distinct from the participant markers - green when placed, blue when selected. */
  readonly rendezvousColor = '#8e4ec6';

  /** Smaller and duller than the pins, so a crowd of them stays readable. */
  readonly eventColor = '#f76b15';

  readonly mapStyle = 'https://tiles.openfreemap.org/styles/liberty';
  readonly center: [number, number] = [16.3738, 48.2082];
  readonly zoom: [number] = [12];

  /** Address results are ranked by distance from here. */
  readonly searchNear: Position = { latitude: this.center[1], longitude: this.center[0] };

  constructor() {
    effect(() => this.meetups.open(this.id()));
  }

  addParticipant() {
    const name = this.newName().trim();
    if (!name) {
      return;
    }
    this.meetups.addParticipant(name);
    this.newName.set('');
  }

  selectParticipant(id: string) {
    this.meetups.selectParticipant(id);
  }

  saveParticipant(edit: ParticipantEdit) {
    this.meetups.saveParticipant(edit.id, edit.name, edit.travelMode);
  }

  setParticipantLocation(location: ParticipantLocation) {
    this.meetups.setParticipantPosition(location.id, location.position, location.label);
  }

  /** Event images are hotlinked from the sources' own servers, so some are
   *  already dead or refuse us; drop those instead of showing a broken icon. */
  hideBrokenImage(event: globalThis.Event) {
    (event.target as HTMLImageElement).remove();
  }

  onMapClick(event: MapMouseEvent) {
    if (this.selectedId()) {
      this.meetups.placeParticipant({
        latitude: event.lngLat.lat,
        longitude: event.lngLat.lng,
      });
    }
  }
}
