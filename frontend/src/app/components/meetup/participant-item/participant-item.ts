import { ChangeDetectionStrategy, Component, input, linkedSignal, output } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MeetupParticipantRead } from '../../../open-api/model/meetup-participant-read';
import { TravelMode } from '../../../open-api/model/travel-mode';

export interface ParticipantEdit {
  id: string;
  name: string;
  travelMode: TravelMode;
}

@Component({
  selector: 'app-participant-item',
  imports: [DecimalPipe, FormsModule],
  templateUrl: './participant-item.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ParticipantItem {
  readonly participant = input.required<MeetupParticipantRead>();
  readonly selected = input(false);

  readonly selectRequested = output<string>();
  readonly save = output<ParticipantEdit>();

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
}
