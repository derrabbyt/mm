import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ErrorFallback } from './error-fallback';

describe('ErrorFallback', () => {
  let component: ErrorFallback;
  let fixture: ComponentFixture<ErrorFallback>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ErrorFallback],
    }).compileComponents();

    fixture = TestBed.createComponent(ErrorFallback);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
