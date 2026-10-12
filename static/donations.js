(() => {
  'use strict';
  document.querySelector('#donate-button').onclick=()=>window.ChartAccounts.dialog('Support ZguaCharts',async content=>{
    content.classList.add('donation-panel');content.textContent='Loading donation options...';
    try{
      const {settings}=await window.ChartAccounts.api('donation');content.replaceChildren();
      for(const [key,label]of [['kofi','Ko-fi'],['stripe','Card via Stripe'],['paypal','PayPal'],['revolut','Revolut']]){
        if(!settings[key])continue;const link=document.createElement('a');link.href=settings[key];link.target='_blank';link.rel='noopener noreferrer';link.textContent=label;content.append(link);
      }
      if(settings.iban){const section=document.createElement('section'),heading=document.createElement('h3');heading.textContent='Bank transfer';section.append(heading);
        for(const [key,label]of [['beneficiary','Beneficiary'],['iban','IBAN'],['bic','BIC / SWIFT'],['bankName','Bank'],['reference','Reference']]){if(!settings[key])continue;const row=document.createElement('p'),name=document.createElement('strong'),value=document.createElement('span');name.textContent=label+' ';value.textContent=settings[key];row.append(name,value);section.append(row);}content.append(section);}
      if(!content.children.length)content.textContent='Donation options are not configured yet.';
    }catch(error){content.textContent=error.message;}
  });
})();
