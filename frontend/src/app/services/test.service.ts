import { inject, Injectable } from '@angular/core';
import { TestDataRequest, TestDataResponse } from '../models/test-model';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class TestService {

  private http = inject(HttpClient);

  sendData(testData: TestDataRequest): Observable<TestDataResponse> {
    console.log('Data sent to the service:', testData);

    return this.http.post<TestDataResponse>('api/test', testData);

  }
}
