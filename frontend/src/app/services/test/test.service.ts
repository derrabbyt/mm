import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { MainTestApiService, TestDataResponse} from '../../open-api';
import { TestDataRequest } from '../../open-api/model/test-data-request';

@Injectable({
  providedIn: 'root',
})
export class TestService {

  private api = inject(MainTestApiService);

  sendData(testData: TestDataRequest): Observable<TestDataResponse> {
    console.log('Data sent to the service:', testData);
    return this.api.testEndpoint({ testDataRequest: testData });


  }
}
