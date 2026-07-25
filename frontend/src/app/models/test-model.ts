export interface TestDataRequest {
  brand: string;
  model: string;
  year: number;
}

export interface TestDataResponse {
  calculatedValue: number;
  status: string;
}

