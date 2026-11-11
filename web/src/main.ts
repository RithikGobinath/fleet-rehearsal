import { bootstrapApplication } from '@angular/platform-browser';
import { App } from './app';
import { inventoryHttp } from './inventory-http';

bootstrapApplication(App, { providers: [inventoryHttp] }).catch(console.error);
