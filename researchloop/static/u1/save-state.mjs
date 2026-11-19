export class SaveQueue {
 constructor(notify){this.notify=notify;this.tail=Promise.resolve();this.failed=null;}
 run(operation){this.tail=this.tail.catch(()=>{}).then(async()=>{this.notify('Pending — not saved');try{const result=await operation();this.failed=null;this.notify('Saved');return result;}catch(error){this.failed=error;this.notify(error.savedOnServer?'Saved on server — refresh failed; retry refresh or reload':navigator.onLine?'Failed — unsaved input retained':'Offline — not saved');throw error;}});return this.tail;}
}
