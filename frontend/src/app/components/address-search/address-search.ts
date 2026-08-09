import { ChangeDetectionStrategy, Component, inject, input, output, signal } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { catchError, debounceTime, distinctUntilChanged, of, switchMap, tap } from 'rxjs';
import { PhotonPlace, PhotonService } from '../../services/photon/photon.service';
import { Position } from '../../open-api/model/position';

/** Long enough to keep us off the public Photon instance while someone types. */
const DEBOUNCE_MS = 350;
const MIN_QUERY_LENGTH = 3;

@Component({
  selector: 'app-address-search',
  imports: [FormsModule],
  templateUrl: './address-search.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AddressSearch {
  private photon = inject(PhotonService);

  readonly placeholder = input('Search address or place');
  /** Biases results towards this point - pass the map centre or the meetup area. */
  readonly near = input<Position | null>(null);
  readonly lang = input('en');
  readonly limit = input(5);

  readonly picked = output<PhotonPlace>();

  readonly query = signal('');
  readonly open = signal(false);
  readonly loading = signal(false);

  readonly results = toSignal(
    toObservable(this.query).pipe(
      debounceTime(DEBOUNCE_MS),
      distinctUntilChanged(),
      tap((query) => this.loading.set(query.trim().length >= MIN_QUERY_LENGTH)),
      // switchMap so a slow response for an older query can never overwrite a
      // newer one - the in-flight request is cancelled instead.
      switchMap((query) => {
        const trimmed = query.trim();
        if (trimmed.length < MIN_QUERY_LENGTH) {
          return of([]);
        }
        return this.photon
          .search(trimmed, { near: this.near(), lang: this.lang(), limit: this.limit() })
          .pipe(
            catchError((err) => {
              console.error('[photon] search failed', err);
              return of([]);
            }),
          );
      }),
      tap(() => this.loading.set(false)),
    ),
    { initialValue: [] as PhotonPlace[] },
  );

  choose(place: PhotonPlace) {
    this.picked.emit(place);
    this.query.set(place.label);
    this.open.set(false);
  }

  /** Deferred so a click on a result lands before the list is torn down. */
  closeSoon() {
    setTimeout(() => this.open.set(false), 150);
  }
}
