import { ChangeDetectionStrategy, Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { MeetupService } from '../../services/meetup/meetup.service';
import { SupabaseService } from '../../services/supabase/supabase.service';

@Component({
  selector: 'app-home',
  imports: [FormsModule, RouterLink],
  templateUrl: './home.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Home {
  private meetups = inject(MeetupService);
  private supabase = inject(SupabaseService);
  private router = inject(Router);

  readonly user = this.supabase.user;
  readonly list = this.meetups.meetups;

  readonly name = signal('');
  readonly location = signal('');
  readonly startsAt = signal('');

  constructor() {
    effect(() => {
      if (this.user()) {
        this.meetups.loadMeetups();
      }
    });
  }

  create() {
    const name = this.name().trim();
    const location = this.location().trim();
    const startsAt = this.startsAt();
    if (!name || !location || !startsAt) {
      return;
    }

    this.meetups
      .create({
        name,
        location,
        starts_at: new Date(startsAt).toISOString(),
      })
      .subscribe({
        next: (meetup) => this.router.navigate(['/meetup', meetup.id]),
        error: (err) => console.error('[meetup] creating meetup failed', err),
      });
  }
}
