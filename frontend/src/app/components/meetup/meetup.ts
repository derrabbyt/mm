import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  effect,
  signal,
} from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { NgxMapLibreGLModule } from '@maplibre/ngx-maplibre-gl';
import type { MapMouseEvent } from 'maplibre-gl';
import { MeetupService } from '../../services/meetup/meetup.service';
import { ParticipantEdit, ParticipantItem } from './participant-item/participant-item';

@Component({
  selector: 'app-meetup',
  imports: [NgxMapLibreGLModule, FormsModule, DecimalPipe, ParticipantItem],
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

  readonly newName = signal('');

  readonly worstMinutes = computed(() => {
    const spot = this.rendezvous();
    return spot ? Math.round(spot.worst_seconds / 60) : null;
  });

  /** Distinct from the participant markers - green when placed, blue when selected. */
  readonly rendezvousColor = '#8e4ec6';

  readonly mapStyle = 'https://tiles.openfreemap.org/styles/liberty';
  readonly center: [number, number] = [16.3738, 48.2082];
  readonly zoom: [number] = [12];

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

  onMapClick(event: MapMouseEvent) {
    if (this.selectedId()) {
      this.meetups.placeParticipant({
        latitude: event.lngLat.lat,
        longitude: event.lngLat.lng,
      });
    }
  }
}
