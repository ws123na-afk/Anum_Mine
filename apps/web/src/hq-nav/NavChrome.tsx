/** The parts of the navigation layer that draw panels. HqNavigation loads them as one lazy chunk, so the headquarters chunk keeps its budget and the list view never downloads them. */
import './hqNav.css';
export { FloorRail } from './FloorRail.tsx';
export { ViewStrip } from './ViewStrip.tsx';
export { FloorPlan, PeopleList, QuickMoves } from './SidePanel.tsx';
