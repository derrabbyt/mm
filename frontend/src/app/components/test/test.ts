import { Component, inject } from '@angular/core';
import { TestService } from '../../services/test.service';
import { TestDataRequest } from '../../open-api/model/test-data-request';

@Component({
  selector: 'app-test',
  imports: [],
  templateUrl: './test.html',
  styleUrl: './test.css',
})
export class Test {

  private testService = inject(TestService);

  sendDummyData() {

    const testData: TestDataRequest = {
      brand: 'Example Brand',
      model: 'Example Model',
      year: 2023,
    }

    console.log('Sending dummy data:', testData);

    this.testService.sendData(testData).subscribe(
      (response) => {
        console.log('Response from the service:', response);
      },
      (error) => {
        console.error('Error from the service:', error);
      }
    );
    
  }
}
