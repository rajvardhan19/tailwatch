'use strict';
// The signed token arrives in the URL fragment, which browsers never send to a server.
// Nothing is changed until the recipient presses the button, so link scanners are inert.
(function(){
  const $=s=>document.querySelector(s);
  const params=new URLSearchParams(location.hash.slice(1));
  history.replaceState(null,'',location.pathname);
  const action=params.get('action'),id=params.get('id')||'',token=params.get('token')||'';
  const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const copy={
    verify:{title:'Confirm alert subscription',text:'Confirm that reviewed Tailwatch research alerts may be sent to this address.',button:'Confirm subscription',path:`/verifications/${id}/confirm`,done:'Confirmed. Reviewed alerts can now be delivered to this address.'},
    unsubscribe:{title:'Unsubscribe',text:'Stop all Tailwatch alert emails for this destination.',button:'Unsubscribe',path:`/deliveries/${id}/unsubscribe`,done:'Unsubscribed. No further alerts will be sent to this destination.'}
  }[action];
  if(!copy||!uuid.test(id)||!/^[A-Za-z0-9_-]{32,128}$/.test(token)){
    $('#confirm-title').textContent='This link is incomplete';
    $('#confirm-text').textContent='Open the full link from your email, or request a new message.';
    return;
  }
  $('#confirm-title').textContent=copy.title;
  $('#confirm-text').textContent=copy.text;
  const button=$('#confirm-action');
  button.textContent=copy.button;
  button.hidden=false;
  button.addEventListener('click',async()=>{
    button.disabled=true;
    try{
      const response=await fetch(copy.path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
      $('#confirm-result').textContent=response.ok?copy.done:'This link is invalid, expired, or was already used.';
      if(response.ok)button.hidden=true;else button.disabled=false;
    }catch(error){
      $('#confirm-result').textContent='The request could not be completed. Try again shortly.';
      button.disabled=false;
    }
  });
})();
