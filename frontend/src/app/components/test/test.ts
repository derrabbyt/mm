import { Component, inject } from '@angular/core';
import { TestService } from '../../services/test.service';
import { TestDataRequest } from '../../models/test-model';

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

    this.testService.sendData(testData);
    
  }
}
