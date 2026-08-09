import {
  ChangeDetectionStrategy,
  Component,
  input,
  linkedSignal,
  output,
  signal,
} from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MeetupParticipantRead } from '../../../open-api/model/meetup-participant-read';
import { Position } from '../../../open-api/model/position';
import { TravelMode } from '../../../open-api/model/travel-mode';
import { AddressSearch } from '../../address-search/address-search';
import { PhotonPlace } from '../../../services/photon/photon.service';

export interface ParticipantEdit {
  id: string;
  name: string;
  travelMode: TravelMode;
}

export interface ParticipantLocation {
  id: string;
  position: Position;
  /** Only set when the user picked a named place; otherwise the name is
   *  reverse geocoded from the coordinates. */
  label?: string;
}

@Component({
  selector: 'app-participant-item',
  imports: [DecimalPipe, FormsModule, AddressSearch],
  templateUrl: './participant-item.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ParticipantItem {
  readonly participant = input.required<MeetupParticipantRead>();
  readonly selected = input(false);
  /** Biases address results towards the meetup's area. */
  readonly near = input<Position | null>(null);
  /** Name of the current position, kept in the UI - the API stores coordinates. */
  readonly addressLabel = input<string | null>(null);

  readonly selectRequested = output<string>();
  readonly save = output<ParticipantEdit>();
  readonly locationPicked = output<ParticipantLocation>();

  /** Progress or failure of the last "Use my location", null when idle. */
  readonly geoStatus = signal<string | null>(null);

  readonly travelModes = Object.values(TravelMode);

  readonly name = linkedSignal(() => this.participant().name);
  readonly travelMode = linkedSignal<TravelMode>(() => this.participant().travel_mode);

  commit() {
    const participant = this.participant();
    const name = this.name().trim();

    if (!name) {
      this.name.set(participant.name);
      return;
    }

    if (name === participant.name && this.travelMode() === participant.travel_mode) {
      return;
    }

    this.save.emit({ id: participant.id, name, travelMode: this.travelMode() });
  }

  pickLocation() {
    this.selectRequested.emit(this.participant().id);
  }

  setLocationFromAddress(place: PhotonPlace) {
    this.locationPicked.emit({
      id: this.participant().id,
      position: place.position,
      label: place.label,
    });
  }

  /** Whoever is at the browser, assigned to this participant. */
  useMyLocation() {
    if (!navigator.geolocation) {
      this.geoStatus.set('This browser has no geolocation');
      return;
    }

    this.geoStatus.set('Locating…');
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        this.geoStatus.set(null);
        this.locationPicked.emit({
          id: this.participant().id,
          position: { latitude: coords.latitude, longitude: coords.longitude },
        });
      },
      // Denied permission is the common case here, so say so rather than
      // leaving the button looking like it did nothing.
      (error) => this.geoStatus.set(error.message || 'Could not get your location'),
    );
  }
}
