import { Injectable } from '@angular/core';

@Injectable({
  providedIn: 'root',
})
export class TestService {

  sendData(data: { brand: string; model: string; year: number }) {
    console.log('Data sent to the service:', data);
  }
}
