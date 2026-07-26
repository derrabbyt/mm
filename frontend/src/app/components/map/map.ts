import { ChangeDetectionStrategy, Component } from '@angular/core';
import {MapComponent} from '@maplibre/ngx-maplibre-gl';

@Component({
  selector: 'app-map',
  standalone: true,
  imports: [MapComponent],
  templateUrl: './map.html',
  styleUrl: './map.css',
  changeDetection: ChangeDetectionStrategy.OnPush,

})
export class Map {

  readonly mapStyle = 'https://tiles.openfreemap.org/styles/liberty';

  // IMPORTANT: MapLibre coordinates are [longitude, latitude]
  readonly center: [number, number] = [16.3738, 48.2082];

  readonly zoom: [number] = [12];
}

