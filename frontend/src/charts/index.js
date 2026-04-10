// Register all Chart.js components once
import {
  Chart,
  BarElement, LineElement, PointElement, ScatterController,
  BarController, LineController,
  CategoryScale, LinearScale,
  Tooltip, Legend, Title,
  Filler,
} from 'chart.js';

Chart.register(
  BarElement, LineElement, PointElement, ScatterController,
  BarController, LineController,
  CategoryScale, LinearScale,
  Tooltip, Legend, Title,
  Filler,
);
