/** The parts of the navigation layer that draw panels. HqNavigation loads them as one lazy chunk, so the headquarters chunk keeps its budget and the list view never downloads them. */
import './hqNav.css';
import './hqPanels.css';
export { FloorRail } from './FloorRail.tsx';
export { ViewStrip } from './ViewStrip.tsx';
export { FloorPlan, PeopleList, QuickMoves } from './SidePanel.tsx';
export { OfficeStatus } from './OfficeStatus.tsx';
export { AssistantPanel } from './AssistantPanel.tsx';
