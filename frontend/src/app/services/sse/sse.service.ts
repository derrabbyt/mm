import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { Item } from '../../open-api/model/item';
import { BASE_PATH } from '../../open-api';

@Injectable({
  providedIn: 'root',
})
export class SSEService {

  private basePath = inject(BASE_PATH);

   streamItems(): Observable<Item> {
    return new Observable<Item>((subscriber) => {
      const source = new EventSource(
        `${this.basePath}/api/sse`
      );

      source.onmessage = (event) => {
        try {
          subscriber.next(JSON.parse(event.data) as Item);
        } catch (error) {
          subscriber.error(error);
        }
      };

      source.onerror = (error) => {
        subscriber.error(error);
      };

      // Called when Angular/RxJS unsubscribes.
      return () => {
        source.close();
      };
    });
  }
}
