OBSERVER AUTOMATON linux_usb_dev
INITIAL STATE Init;

STATE USEALL Init :
  MATCH ENTRY -> ENCODE {int usb_dev_state = 0;} GOTO Init;

  MATCH RETURN {$2=ldv_usb_get_dev($1)} -> ASSUME {((struct usb_device *)$1) != 0; ((struct usb_device *)$2) != 0} ENCODE {usb_dev_state=usb_dev_state+1;} GOTO Inc;
  MATCH RETURN {$2=ldv_usb_get_dev($1)} -> ASSUME {((struct usb_device *)$1) == 0; ((struct usb_device *)$2) == 0} GOTO Init;
  MATCH RETURN {$2=ldv_usb_get_dev($1)} -> ASSUME {((struct usb_device *)$1) == 0; ((struct usb_device *)$2) != 0} GOTO Stop;
  MATCH RETURN {$2=ldv_usb_get_dev($1)} -> ASSUME {((struct usb_device *)$1) != 0; ((struct usb_device *)$2) == 0} GOTO Stop;
  
  MATCH CALL {ldv_usb_put_dev($1)} -> ASSUME {((struct usb_device *)$1) != 0} ERROR("linux:usb:dev::unincremented counter decrement");
  MATCH CALL {ldv_usb_put_dev($1)} -> ASSUME {((struct usb_device *)$1) != 0} ERROR("linux:usb:dev::less initial decrement");
  MATCH CALL {ldv_usb_put_dev($1)} -> ASSUME {((struct usb_device *)$1) == 0} GOTO Init;

STATE USEALL Inc :
  MATCH RETURN {$2=ldv_usb_get_dev($1)} -> ASSUME {((struct usb_device *)$1) != 0; ((struct usb_device *)$2) != 0} ENCODE {usb_dev_state=usb_dev_state+1;} GOTO Inc;
  MATCH RETURN {$2=ldv_usb_get_dev($1)} -> ASSUME {((struct usb_device *)$1) == 0; ((struct usb_device *)$2) == 0} GOTO Inc;
  MATCH RETURN {$2=ldv_usb_get_dev($1)} -> ASSUME {((struct usb_device *)$1) == 0; ((struct usb_device *)$2) != 0} GOTO Stop;
  MATCH RETURN {$2=ldv_usb_get_dev($1)} -> ASSUME {((struct usb_device *)$1) != 0; ((struct usb_device *)$2) == 0} GOTO Stop;

  MATCH CALL {ldv_usb_put_dev($1)} -> ASSUME {((struct usb_device *)$1) != 0; usb_dev_state >  1} ENCODE {usb_dev_state=usb_dev_state-1;} GOTO Inc;
  MATCH CALL {ldv_usb_put_dev($1)} -> ASSUME {((struct usb_device *)$1) != 0; usb_dev_state == 1} ENCODE {usb_dev_state=usb_dev_state-1;} GOTO Init;
  MATCH CALL {ldv_usb_put_dev($1)} -> ASSUME {((struct usb_device *)$1) == 0} GOTO Inc;

  MATCH CALL {ldv_check_return_value_probe($1)} -> ASSUME {((int)$1) == 0} GOTO Inc;
  MATCH CALL {ldv_check_return_value_probe($1)} -> ASSUME {((int)$1) != 0} ERROR("linux:usb:dev::probe failed");

  MATCH CALL {ldv_check_final_state($?)} -> ERROR("linux:usb:dev::more initial at exit");

STATE USEFIRST Stop :
  TRUE -> GOTO Stop;

END AUTOMATON