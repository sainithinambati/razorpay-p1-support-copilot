"""Deterministic reply builder (English and Hinglish).

Every sentence here comes from a KB article or the merchant record. These replies are:
  - the approved base draft that Claude is allowed to rephrase, and
  - the safe fallback when Claude is unavailable or its draft fails validation.
"""
from .text import format_inr


def _t(lang, en, hi):
    return hi if lang == "Hinglish" else en


def greeting(lang, name):
    return _t(lang, f"Hi {name} team,", f"Namaste {name} team,")


def signoff(lang):
    return _t(lang, "Regards,\nRazorpay Support", "Dhanyavaad,\nRazorpay Support")


def empathy(lang, tone, critical=False):
    if critical:
        return _t(lang, "Thank you for bringing this to our attention. We understand your concern and are treating it seriously.",
                  "Yeh baat humare dhyan mein laane ke liye dhanyavaad. Hum aapki chinta samajhte hain aur ise gambhirta se le rahe hain.")
    if tone == "angry":
        return _t(lang, "I understand how frustrating this is, and I'm sorry for the trouble it is causing your business.",
                  "Hum samajhte hain ki yeh kitna frustrating hai, aur aapke business ko ho rahi pareshani ke liye maafi chahte hain.")
    if tone == "frustrated":
        return _t(lang, "I understand this is causing you inconvenience, and I'm sorry about that.",
                  "Hum samajh sakte hain ki isse aapko pareshani ho rahi hai, iske liye maafi chahte hain.")
    return _t(lang, "Thanks for reaching out.", "Humse sampark karne ke liye dhanyavaad.")


def body(d, lang, p, ext):
    f = d.facts
    tpl = d.template
    v = d.variant or {}

    if tpl == "critical_ack":
        lines = [_t(lang, "Your message has been escalated to the appropriate team for immediate review, and they will contact you directly.",
                    "Aapka message turant review ke liye sambandhit team ko escalate kar diya gaya hai, aur woh aapse seedhe sampark karenge.")]
        if v.get("security"):
            lines.append(_t(lang, "For your safety, please do not share passwords, OTPs or API keys with anyone, including anyone claiming to be from Razorpay.",
                            "Aapki suraksha ke liye, kripya apna password, OTP ya API keys kisi ke saath share na karein, chahe koi Razorpay se hone ka daawa kare."))
        return lines

    if tpl == "identity_conflict":
        return [_t(lang, "We need to verify a few account details before we can look into this, so your request has been passed to the appropriate team. They will contact you directly.",
                   "Is par aage badhne se pehle humein kuch account details verify karni hongi, isliye aapki request sambandhit team ko bhej di gayi hai. Woh aapse seedhe sampark karenge.")]

    if tpl == "ask_merchant_id":
        if "payment_id" in d.ask_for:
            return [_t(lang, "To look into this, please share your Razorpay merchant ID (it starts with 'M') and the payment ID of the refund (it starts with 'pay_').",
                       "Is par check karne ke liye, kripya apna Razorpay merchant ID (jo 'M' se shuru hota hai) aur refund ka payment ID (jo 'pay_' se shuru hota hai) share karein.")]
        return [_t(lang, "To look into this, please share your Razorpay merchant ID (it starts with 'M') and a short description of the issue.",
                   "Is par check karne ke liye, kripya apna Razorpay merchant ID (jo 'M' se shuru hota hai) aur issue ki thodi detail share karein.")]

    if tpl == "ask_details":
        return [_t(lang, "Could you share a few more details: what you were trying to do, any payment ID or settlement reference involved, and any error message you saw? That will help us check this for you.",
                   "Kya aap thodi aur details share kar sakte hain: aap kya karne ki koshish kar rahe the, koi payment ID ya settlement reference, aur koi error message jo aapne dekha ho? Isse hum aapke liye check kar payenge.")]

    if tpl == "ask_payment_details":
        return [_t(lang, "Could you share the payment ID, the payment method used (UPI, card or netbanking), the approximate time of the attempt and any error message shown? That will help us check this payment.",
                   "Kya aap payment ID, payment method (UPI, card ya netbanking), payment ka approximate time aur jo error message dikha ho, woh share kar sakte hain? Isse hum is payment ko check kar payenge.")]

    if tpl == "ask_payment_id":
        return [_t(lang, "To check this refund, please share the payment ID (it starts with 'pay_').",
                   "Is refund ko check karne ke liye, kripya payment ID (jo 'pay_' se shuru hota hai) share karein.")]

    if tpl == "ask_refund_amount":
        return [_t(lang, "To check this refund, please confirm the refund amount for this payment.",
                   "Is refund ko check karne ke liye, kripya is payment ka refund amount confirm karein.")]

    if tpl == "settlement_settled":
        amt = format_inr(f["last_settlement_amount_inr"]) if f.get("last_settlement_amount_inr") is not None else None
        date = f.get("last_settlement_date")
        lines = []
        if amt and date:
            lines.append(_t(lang, f"Our records show your settlement status is Settled, and your last settlement of Rs. {amt} was credited on {date}.",
                            f"Humare records ke hisaab se aapka settlement status Settled hai, aur aapka last settlement Rs. {amt} {date} ko credit hua tha."))
        lines.append(_t(lang, f"Standard settlements are credited T+{p['settlement_tplus_days']} business days after payment capture, excluding weekends and bank holidays. You can track each settlement under Dashboard > Settlements.",
                        f"Standard settlements payment capture ke T+{p['settlement_tplus_days']} business days mein credit hote hain (weekends aur bank holidays chhodkar). Aap har settlement Dashboard > Settlements mein track kar sakte hain."))
        lines.append(_t(lang, "If a specific amount is still missing, please share the payment date range and we will check it.",
                        "Agar koi specific amount abhi bhi missing hai, toh kripya payment date range share karein, hum check karenge."))
        return lines

    if tpl == "settlement_delayed":
        return [_t(lang, f"Your latest settlement is delayed because of bank-side processing. As per our system, the expected settlement date is {f['expected_settlement_date']}.",
                   f"Aapka latest settlement bank-side processing ki wajah se delayed hai. Humare system ke hisaab se expected settlement date {f['expected_settlement_date']} hai."),
                _t(lang, f"Bank-side processing can add up to {p['bank_delay_extra_business_days']} business days to the standard timeline. You can track it under Dashboard > Settlements.",
                   f"Bank-side processing standard timeline mein {p['bank_delay_extra_business_days']} business days tak add kar sakti hai. Aap ise Dashboard > Settlements mein track kar sakte hain.")]

    if tpl == "settlement_kyc_hold":
        return [_t(lang, "Your settlements are on hold because your KYC is pending. Please complete the pending documents in Dashboard > Account > KYC.",
                   "Aapke settlements hold par hain kyunki aapka KYC pending hai. Kripya Dashboard > Account > KYC mein pending documents complete karein."),
                _t(lang, f"Once your KYC is verified, held settlements are released within {p['kyc_release_business_days']} business day.",
                   f"KYC verify hone ke baad, hold kiye gaye settlements {p['kyc_release_business_days']} business day ke andar release hote hain.")]

    if tpl == "settlement_bank_mismatch":
        return [_t(lang, "Your settlements are on hold because the name on your bank account does not match your registered business or PAN name.",
                   "Aapke settlements hold par hain kyunki aapke bank account ka naam aapke registered business ya PAN naam se match nahi kar raha."),
                _t(lang, f"Please re-add the correct account in Dashboard > Settings > Bank Account. Re-verification (penny-drop) completes within {p['penny_drop_hours']} hours, and settlements can proceed only after the account is verified.",
                   f"Kripya Dashboard > Settings > Bank Account mein sahi account dobara add karein. Re-verification (penny-drop) {p['penny_drop_hours']} hours mein complete hota hai, aur account verify hone ke baad hi settlements aage badh sakte hain.")]

    if tpl == "settlement_hold_escalate":
        return [_t(lang, "I can see that settlements on your account are currently on hold. This type of hold cannot be released or changed from support, so I have escalated your case to our specialist team for review.",
                   "Hum dekh sakte hain ki aapke account par settlements abhi hold par hain. Is tarah ka hold support se release ya change nahi kiya ja sakta, isliye aapka case review ke liye humari specialist team ko escalate kar diya gaya hai."),
                _t(lang, "They will update you once the review is complete. You can also check the current status under Dashboard > Settlements.",
                   "Review complete hone par woh aapko update karenge. Aap current status Dashboard > Settlements mein bhi dekh sakte hain.")]

    if tpl == "payment_degradation":
        lines = [_t(lang, f"Thanks for flagging this. Your 7-day payment success rate is {f['payment_success_rate_7d_pct']}%, which is lower than expected, so I have escalated this to our payments team to investigate a possible issue on the processing side.",
                    f"Yeh batane ke liye dhanyavaad. Aapka 7-day payment success rate {f['payment_success_rate_7d_pct']}% hai, jo expected se kam hai, isliye humne ise processing side par possible issue ki jaanch ke liye humari payments team ko escalate kar diya hai.")]
        if v.get("deducted"):
            lines.append(_t(lang, f"If a customer's money was deducted but the order failed, the amount is automatically reversed to the customer within {p['auto_reversal_business_days']} business days.",
                            f"Agar customer ka paisa deduct hua lekin order fail ho gaya, toh woh amount {p['auto_reversal_business_days']} business days mein customer ko automatically reverse ho jata hai."))
        lines.append(_t(lang, "The team will update you once they have reviewed it.", "Review ke baad team aapko update karegi."))
        return lines

    if tpl == "payment_failure":
        lines = [_t(lang, "Payment failures usually happen because of bank declines, insufficient funds, UPI app time-outs or incorrect card details. Please ask affected customers to retry or use another payment method.",
                    "Payment failures aam taur par bank decline, insufficient funds, UPI app time-out ya galat card details ki wajah se hote hain. Kripya affected customers ko dobara try karne ya doosra payment method use karne ke liye kahein."),
                 _t(lang, f"Your 7-day payment success rate is {f['payment_success_rate_7d_pct']}%, which is within the normal range.",
                    f"Aapka 7-day payment success rate {f['payment_success_rate_7d_pct']}% hai, jo normal range mein hai.")]
        if v.get("deducted"):
            lines.append(_t(lang, f"If money was deducted but the order failed, the amount is automatically reversed to the customer within {p['auto_reversal_business_days']} business days.",
                            f"Agar paisa deduct hua lekin order fail ho gaya, toh woh amount {p['auto_reversal_business_days']} business days mein customer ko automatically reverse ho jata hai."))
        lines.append(_t(lang, "If failures continue, please share a few payment IDs and we will check them.",
                        "Agar failures jaari rahein, toh kripya kuch payment IDs share karein, hum unhe check karenge."))
        return lines

    if tpl == "refund_high_value":
        return [_t(lang, f"Refunds of Rs. {format_inr(p['high_value_refund_inr'])} or more are reviewed and approved manually by our refunds team, so I have escalated your refund for payment {f['payment_id']} (Rs. {format_inr(f['refund_amount_inr'])}).",
                   f"Rs. {format_inr(p['high_value_refund_inr'])} ya usse zyada ke refunds humari refunds team manually review aur approve karti hai, isliye payment {f['payment_id']} (Rs. {format_inr(f['refund_amount_inr'])}) ka refund escalate kar diya gaya hai."),
                _t(lang, "We are not able to share a timeline until the team has reviewed it, and they will update you.",
                   "Team ke review se pehle hum koi timeline share nahi kar sakte, woh aapko update karenge.")]

    if tpl == "refund_standard":
        return [_t(lang, f"Refunds are credited to the customer's original payment source within {p['refund_credit_business_days']} business days. An ARN (Acquirer Reference Number) becomes available {p['arn_available_after_days']} days after the refund is processed.",
                   f"Refunds customer ke original payment source mein {p['refund_credit_business_days']} business days mein credit hote hain. Refund process hone ke {p['arn_available_after_days']} din baad ARN (Acquirer Reference Number) available ho jata hai."),
                _t(lang, f"For payment {f['payment_id']}, you can share the ARN with your customer so their bank can trace the credit.",
                   f"Payment {f['payment_id']} ke liye, aap ARN apne customer ke saath share kar sakte hain taaki unka bank credit trace kar sake.")]

    if tpl == "chargeback":
        lines = []
        if v.get("block"):
            lines.append(_t(lang, "Disputes cannot be blocked in advance. If a customer does raise one, you can respond to it with evidence.",
                            "Disputes ko pehle se block nahi kiya ja sakta. Agar customer dispute raise karta hai, toh aap evidence ke saath uska jawab de sakte hain."))
        if v.get("no_notice"):
            lines.append(_t(lang, "Please check Dashboard > Disputes, where the notice for this dispute is shown.",
                            "Kripya Dashboard > Disputes check karein, wahan is dispute ka notice dikhega."))
        lines.append(_t(lang, f"When a customer disputes a payment, you get a notice on the Dashboard under Disputes. You must upload evidence (invoice, proof of delivery and communication with the customer) within {p['chargeback_evidence_days']} calendar days. If the deadline is missed, the dispute is lost automatically.",
                        f"Jab customer kisi payment ko dispute karta hai, toh aapko Dashboard ke Disputes section mein notice milta hai. Aapko {p['chargeback_evidence_days']} calendar days ke andar evidence (invoice, proof of delivery aur customer ke saath communication) upload karna hoga. Deadline miss hone par dispute automatically lost ho jata hai."))
        return lines

    if tpl == "pricing_escalate":
        return [_t(lang, "Pricing changes, fee waivers and refunds of fee differences are handled only by your Account Manager, so I have shared your request with them.",
                   "Pricing changes, fee waivers aur fee difference ke refunds sirf aapke Account Manager handle karte hain, isliye aapki request unhe bhej di gayi hai."),
                _t(lang, "Support is not able to approve or commit to any change, but your Account Manager will review it and get in touch.",
                   "Support se koi change approve ya commit nahi kiya ja sakta, lekin aapke Account Manager ise review karke aapse sampark karenge.")]

    if tpl == "invoice":
        lines = [_t(lang, f"GST invoices for fees are generated by the {p['invoice_generation_day']}th of every month and are available under Dashboard > Reports > Invoices.",
                    f"Fees ke GST invoices har mahine ki {p['invoice_generation_day']} tareekh tak generate hote hain aur Dashboard > Reports > Invoices mein available hote hain.")]
        if f.get("gst_invoice_available") == "No":
            lines.append(_t(lang, "Our records currently show no GST invoice available on your account. If it does not appear there, please reply here and we will look into it.",
                            "Humare records mein abhi aapke account par GST invoice available nahi dikh raha. Agar wahan na dikhe, toh kripya yahin reply karein, hum check karenge."))
        return lines

    if tpl == "kyc_documents":
        return [_t(lang, "The documents required for KYC are: PAN of the business or owner, address proof, bank account proof, GST certificate (if GST-registered), and ID of the authorised signatory.",
                   "KYC ke liye ye documents chahiye: business ya owner ka PAN, address proof, bank account proof, GST certificate (agar GST-registered hain), aur authorised signatory ka ID.")]

    if tpl == "kyc_reverification":
        lines = [_t(lang, "Genuine re-verification requests come only from your Razorpay Dashboard or from @example.com email addresses. If the request is not on your Dashboard and came from anywhere else, please do not act on it or share any documents through it.",
                    "Genuine re-verification requests sirf aapke Razorpay Dashboard ya @example.com email addresses se aati hain. Agar request aapke Dashboard par nahi hai aur kahin aur se aayi hai, toh kripya us par action na lein aur koi document share na karein.")]
        if f.get("kyc_status") == "Re-verification needed":
            lines.append(_t(lang, "Your account currently shows a re-verification request, so please complete it from your Dashboard.",
                            "Aapke account mein abhi re-verification request dikh rahi hai, isliye kripya ise apne Dashboard se complete karein."))
        lines.append(_t(lang, f"Re-verification can be triggered after {p['kyc_reverification_months']} months or when your details change.",
                        f"Re-verification {p['kyc_reverification_months']} months ke baad ya details badalne par trigger ho sakta hai."))
        return lines

    if tpl == "account_change":
        if v.get("legal_change"):
            return [_t(lang, "A change of legal entity name or business type cannot be made by editing the account; it requires a fresh application.",
                       "Legal entity ka naam ya business type account edit karke change nahi kiya ja sakta; iske liye fresh application deni hogi.")]
        return [_t(lang, "You can change your registered email and mobile number in Settings. The change is confirmed with OTP verification.",
                   "Aap apna registered email aur mobile number Settings mein change kar sakte hain. Yeh change OTP verification se confirm hota hai.")]

    if tpl == "closure":
        pending = []
        if f.get("settlement_status") and f["settlement_status"] != "Settled":
            pending.append(_t(lang, "a settlement that is not yet completed", "ek settlement jo abhi complete nahi hua hai"))
        if f.get("refunds_in_process"):
            pending.append(_t(lang, f"{f['refunds_in_process']} refund(s) in process", f"{f['refunds_in_process']} refund(s) process mein"))
        if f.get("open_chargebacks"):
            pending.append(_t(lang, f"{f['open_chargebacks']} open dispute(s)", f"{f['open_chargebacks']} open dispute(s)"))
        lines = [_t(lang, "Account closure is processed only after all pending settlements, refunds and disputes are cleared, so it cannot be done immediately.",
                    "Account closure tabhi process hota hai jab saare pending settlements, refunds aur disputes clear ho jaate hain, isliye yeh turant nahi ho sakta.")]
        if pending:
            lines.append(_t(lang, "Our records show " + ", ".join(pending) + " on your account.",
                            "Humare records ke hisaab se aapke account par " + ", ".join(pending) + " hain."))
        lines.append(_t(lang, "I have passed your request to your Account Manager and the team that handles closures, and they will guide you through the next steps.",
                        "Aapki request aapke Account Manager aur closures handle karne wali team ko bhej di gayi hai, woh aapko aage ke steps batayenge."))
        return lines

    if tpl == "api_signature":
        return [_t(lang, "Please verify the signature using your key secret on the string order_id|payment_id (the order ID, a pipe character, then the payment ID). Keep the key secret on your server and never share it with anyone, including support.",
                   "Kripya signature ko apne key secret se order_id|payment_id string par verify karein (order ID, phir pipe character, phir payment ID). Key secret sirf apne server par rakhein aur kisi ke saath share na karein, support ke saath bhi nahi.")]

    if tpl == "api_amount":
        return [_t(lang, "Amounts must be sent in paise, not rupees. For example, Rs. 499 should be sent as 49900.",
                   "Amount paise mein bhejna hota hai, rupees mein nahi. Jaise Rs. 499 ko 49900 bhejna hoga.")]

    if tpl == "webhook":
        return [_t(lang, f"Please check that your webhook URL is publicly reachable, returns HTTP 200 within {p['webhook_timeout_seconds']} seconds, and that the event is subscribed in your webhook settings.",
                   f"Kripya check karein ki aapka webhook URL publicly reachable hai, {p['webhook_timeout_seconds']} seconds ke andar HTTP 200 return karta hai, aur event aapki webhook settings mein subscribed hai."),
                _t(lang, f"Failed webhooks are retried for up to {p['webhook_retry_hours']} hours.",
                   f"Failed webhooks {p['webhook_retry_hours']} hours tak retry hote hain.")]

    if tpl == "test_live_keys":
        return [_t(lang, "'Authentication failed' on a live site usually means test keys are being used in production. Please use the live key ID and secret generated after activation, and keep the secret on your server only.",
                   "Live site par 'Authentication failed' ka matlab aam taur par yeh hota hai ki production mein test keys use ho rahi hain. Kripya activation ke baad generate hui live key ID aur secret use karein, aur secret sirf apne server par rakhein.")]

    if tpl == "tech_escalate":
        return [_t(lang, "I have escalated this to our technical support team to investigate.",
                   "Humne ise jaanch ke liye humari technical support team ko escalate kar diya hai."),
                _t(lang, "To help them, please share request IDs, timestamps and any error responses you have. Please never share your API key secret.",
                   "Unki madad ke liye, kripya request IDs, timestamps aur koi bhi error responses share karein. Kripya apna API key secret kabhi share na karein.")]

    if tpl == "generic_escalate":
        return [_t(lang, "I have passed your request to our specialist team, who will review it and update you.",
                   "Aapki request humari specialist team ko bhej di gayi hai, woh ise review karke aapko update karenge.")]

    raise ValueError(f"Unknown template {tpl}")


def build_reply(d, lang, tone, params, ext, merchant_name=None) -> str:
    name = merchant_name or _t(lang, "there", "")
    critical = d.risk_level == "Critical"
    parts = [greeting(lang, name).replace(" there team,", " there,").replace("Namaste  team,", "Namaste,"),
             empathy(lang, tone, critical)]
    parts += body(d, lang, params, ext)
    return parts[0] + "\n\n" + " ".join(parts[1:]) + "\n\n" + signoff(lang)


def fallback_ack(lang, action) -> str:
    """Last-resort reply if even the template cannot pass validation."""
    if action == "ASK_FOR_INFO":
        return _t(lang, "Hi,\n\nThanks for reaching out. Could you share your merchant ID and a few more details so we can check this for you?\n\nRegards,\nRazorpay Support",
                  "Namaste,\n\nHumse sampark karne ke liye dhanyavaad. Kripya apna merchant ID aur thodi aur details share karein taaki hum ise check kar sakein.\n\nDhanyavaad,\nRazorpay Support")
    return _t(lang, "Hi,\n\nThanks for reaching out. We have received your request and a member of our team will review it and get back to you.\n\nRegards,\nRazorpay Support",
              "Namaste,\n\nHumse sampark karne ke liye dhanyavaad. Humein aapki request mil gayi hai aur humari team ka ek member ise review karke aapse sampark karega.\n\nDhanyavaad,\nRazorpay Support")
