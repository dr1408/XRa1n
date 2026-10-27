/* NewTerm's tty setup with the device's actual root home and rootless PATH.
 *
 * Ported from Liter8-iPad8 device/appfix/newterm-helper.c for XRa1n. This
 * replaces /var/jb/Applications/NewTerm.app/NewTermLoginHelper. The adapter
 * at /var/jb/usr/bin/login (newterm-login.c) exec's real login as root
 * through a persona-99 posix_spawn child; real login then exec's this
 * helper with its argv shape. We acquire a controlling tty, force a sane
 * root environment, and exec the user shell as a login shell. */
#include <unistd.h>
#include <sys/ioctl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <os/log.h>
int main(int argc,char **argv){
 if(argc<3 || strcmp(argv[0],"-NewTermLoginHelper"))return 2;
 if(setsid()<0)perror("setsid");
 if(ioctl(0,TIOCSCTTY,0))perror("TIOCSCTTY");
 if(getuid()==0){
  setenv("HOME","/var/root",1);setenv("ZDOTDIR","/var/root",1);
  setenv("CFFIXED_USER_HOME","/var/root",1);setenv("USER","root",1);setenv("LOGNAME","root",1);
  setenv("PATH","/var/jb/usr/local/bin:/var/jb/usr/bin:/var/jb/bin:/var/jb/usr/sbin:/var/jb/sbin:/usr/bin:/bin:/usr/sbin:/sbin",1);
  setenv("SHELL","/var/jb/bin/zsh",1);setenv("TMPDIR","/var/jb/tmp",1);
  if(chdir("/var/root"))return 3;
 }else if(chdir(argv[1]))return 3;
 os_log_error(OS_LOG_DEFAULT,"xra1nnewterm: uid=%d home=%{public}s path=%{public}s",getuid(),getenv("HOME"),getenv("PATH"));
 char *program=argv[2];char *base=strrchr(program,'/');base=base?base+1:program;
 char *loginName=malloc(strlen(base)+2);if(!loginName)return 4;
 loginName[0]='-';strcpy(loginName+1,base);argv[2]=loginName;
 execv(program,argv+2);perror("shell exec");return 1;
}
