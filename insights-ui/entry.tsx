import {createRoot} from 'react-dom/client';
import Dashboard from './app/dashboard';
import './app/globals.css';
createRoot(document.getElementById('insights-root')!).render(<Dashboard/>);
