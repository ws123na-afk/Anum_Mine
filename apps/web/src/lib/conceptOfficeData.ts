// Concept preview only: example rooms and example staff positions of the illustrative office layouts.
// Every string here is sample content shown under the "Concept preview" tab, labelled as an example. It is never
// sent to the API, never stored and never mapped to a real person (test/conceptOfficeData.test.ts guards this).
export type Dept = { id: string; name: string; role: string; people: string[]; x: number; y: number; width: number; height: number; floor: number; accent: string };
export const ROOMS: Dept[] = [
  { id: 'reception', name: 'Reception', role: 'Visitors and scheduling', people: ['Reception coordinator', 'Security'], x: 6, y: 65, width: 27, height: 26, floor: 0, accent: '#e8ba74' },
  { id: 'command', name: 'ANUM Command', role: 'Chief of Staff and task oversight', people: ['ANUM - Chief of Staff', 'Automation Guardian'], x: 36, y: 39, width: 30, height: 30, floor: 0, accent: '#4eccc3' },
  { id: 'meeting', name: 'Boardroom', role: 'Live and scheduled meetings', people: ['Meeting coordinator'], x: 70, y: 65, width: 24, height: 25, floor: 0, accent: '#80a7ec' },
  { id: 'finance', name: 'Finance', role: 'Budgets, reconciliation and approvals', people: ['Finance Controller'], x: 6, y: 5, width: 27, height: 29, floor: 1, accent: '#edc88d' },
  { id: 'sales', name: 'Sales & Marketing', role: 'Proposals, pipeline and campaigns', people: ['Fleet Sales Pipeline', 'Fleet Proposal Consultant', 'Marketing Studio'], x: 36, y: 5, width: 30, height: 29, floor: 1, accent: '#89c1fd' },
  { id: 'support', name: 'Customer Experience', role: 'Customer queries and service', people: ['Customer Care', 'Product Support'], x: 70, y: 5, width: 24, height: 29, floor: 1, accent: '#f2b0b6' },
  { id: 'tyre', name: 'Tyre Engineering', role: 'Planning and fleet inspection', people: ['Tyre Fleet Planner', 'Tyre Analyst'], x: 6, y: 40, width: 27, height: 25, floor: 1, accent: '#7dc9a5' },
  { id: 'operations', name: 'Operations', role: 'Warehouse and asset operations', people: ['Operations Assistant'], x: 36, y: 40, width: 30, height: 25, floor: 1, accent: '#9dc6ed' },
  { id: 'systems', name: 'Systems & Knowledge', role: 'Knowledge and automation', people: ['Knowledge Steward', 'Automation Guardian'], x: 70, y: 40, width: 24, height: 25, floor: 1, accent: '#c0a4ef' },
  { id: 'executive', name: 'Executive Office', role: 'Owner and Chief of Staff', people: ['ANUM - Chief of Staff'], x: 6, y: 8, width: 42, height: 38, floor: 2, accent: '#edce93' },
  { id: 'strategy', name: 'Strategy Room', role: 'Planning and decisions', people: ['ANUM - Chief of Staff', 'Finance Controller'], x: 53, y: 8, width: 41, height: 38, floor: 2, accent: '#9bbbe5' },
  { id: 'lounge', name: 'Executive Lounge', role: 'Visitor conversations', people: [], x: 27, y: 56, width: 47, height: 30, floor: 2, accent: '#90bba9' },
];
// These layouts are illustrative previews, not mappings of actual workspace agents or task statuses.
export const SITE_ROOMS: Dept[][] = [ROOMS, [
  {id:'reception',name:'Fleet Reception',role:'Customer enquiries and inspection appointments',people:['Reception coordinator'],x:5,y:61,width:27,height:28,floor:0,accent:'#f1c689'},
  {id:'inspection',name:'Inspection Control',role:'Tyre condition, serial position and inspection schedules',people:['Tyre Fleet Planner','Tyre Analyst'],x:36,y:8,width:30,height:40,floor:0,accent:'#8bdfd2'},
  {id:'workshop',name:'Workshop Control',role:'Service bays, defects and maintenance coordination',people:['Operations Assistant'],x:70,y:9,width:24,height:38,floor:0,accent:'#90adf0'},
  {id:'fleet',name:'Fleet Planning',role:'Tyre rotations and fleet utilization',people:['Tyre Fleet Planner'],x:6,y:8,width:27,height:38,floor:1,accent:'#84dca2'},
  {id:'procurement',name:'Procurement',role:'Tyre requests and supplier proposals',people:['Fleet Proposal Consultant'],x:37,y:12,width:28,height:39,floor:1,accent:'#efd39e'},
  {id:'analysis',name:'Performance Analysis',role:'Tyre life, cost per kilometre and failure patterns',people:['Tyre Analyst'],x:70,y:10,width:24,height:38,floor:1,accent:'#a4bffc'},
  {id:'boardroom',name:'Fleet Boardroom',role:'Performance reviews and inspection decisions',people:['ANUM - Chief of Staff'],x:28,y:62,width:44,height:28,floor:2,accent:'#e7bc98'}
], [
  {id:'reception',name:'Customer Reception',role:'Customer enquiries and order enquiries',people:['Customer Care'],x:7,y:64,width:29,height:28,floor:0,accent:'#f7c798'},
  {id:'care',name:'Customer Care',role:'Multichannel customer support',people:['Customer Care','Product Support'],x:41,y:8,width:26,height:42,floor:0,accent:'#88e0d2'},
  {id:'orders',name:'Orders Desk',role:'Order review, fulfilment and returns',people:['Commerce Coordinator'],x:71,y:8,width:23,height:42,floor:0,accent:'#a8cbf4'},
  {id:'sales',name:'Marketplace Sales',role:'Sales channels, campaigns and lead follow-up',people:['Marketing Studio','Commerce Coordinator'],x:5,y:9,width:28,height:37,floor:1,accent:'#ecbaeb'},
  {id:'warehouse',name:'Dispatch & Logistics',role:'Packing handovers and shipping coordination',people:['Commerce Coordinator'],x:37,y:12,width:30,height:38,floor:1,accent:'#bddca1'},
  {id:'strategy',name:'Commerce Review',role:'Customer performance and business reviews',people:['ANUM - Chief of Staff'],x:29,y:59,width:43,height:32,floor:2,accent:'#eac58e'}
], [
  {id:'welcome',name:'Personal Reception',role:'Private scheduling and guest welcome',people:['Personal assistant'],x:6,y:59,width:32,height:32,floor:0,accent:'#e8c390'},
  {id:'focus',name:'Work & Focus',role:'Personal planning and daily priorities',people:['Personal assistant'],x:42,y:9,width:51,height:37,floor:0,accent:'#a6d6c6'},
  {id:'career',name:'Career Office',role:'Employment responsibilities and professional goals',people:['Personal assistant'],x:6,y:8,width:29,height:35,floor:1,accent:'#b3c9f3'},
  {id:'finance',name:'Personal Finance',role:'Budgeting, costs and financial reviews',people:['Finance Controller'],x:42,y:8,width:52,height:37,floor:1,accent:'#e8ce90'},
  {id:'library',name:'Private Knowledge',role:'Notes, documents and research',people:['Knowledge Steward'],x:27,y:55,width:46,height:33,floor:2,accent:'#aec9e6'}
]];
