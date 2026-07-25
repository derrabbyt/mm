import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { MainTestApiService, TestDataResponse} from '../open-api';
import { TestDataRequest } from '../open-api/model/test-data-request'; //why the fuck do i have to do this here????

@Injectable({
  providedIn: 'root',
})
export class TestService {

  // Uncomment the following line if you want to use HttpClient directly instead of the generated service
  //private http = inject(HttpClient);

  private api = inject(MainTestApiService);

  sendData(testData: TestDataRequest): Observable<TestDataResponse> {
    console.log('Data sent to the service:', testData);
    return this.api.testEndpoint({ testDataRequest: testData });


    // Uncomment the following line if you want to use HttpClient directly instead of the generated service
    //return this.http.post<TestDataResponse>('api/test', testData);

  }
}
