import { Component } from '@angular/core';
import { TestService } from '../../services/test.service';

@Component({
  selector: 'app-test',
  imports: [],
  templateUrl: './test.html',
  styleUrl: './test.css',
})
export class Test {

  constructor(private testService: TestService) {}


  sendDummyData() {
    const data = {
      brand: 'Example Brand',
      model: 'Example Model',
      year: 2023,
    };

    console.log('Sending dummy data:', data);
    
    this.testService.sendData(data);
    
  }
}
