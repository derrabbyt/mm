import { ChangeDetectionStrategy, Component, inject, input, effect, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgxMapLibreGLModule } from '@maplibre/ngx-maplibre-gl';
import type { MapMouseEvent } from 'maplibre-gl';
import { MeetupService } from '../../services/meetup/meetup.service';
import { ParticipantEdit, ParticipantItem } from './participant-item/participant-item';

@Component({
  selector: 'app-meetup',
  imports: [NgxMapLibreGLModule, FormsModule, ParticipantItem],
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

  readonly newName = signal('');

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
