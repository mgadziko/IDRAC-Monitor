using System;
using System.Diagnostics;
using System.Drawing;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Forms;

class Program { [STAThread] static void Main() { Application.EnableVisualStyles(); Application.Run(new Monitor()); } }

class Monitor : Form {
  const string Ipmi = @"C:\ipmitool\192.168.4.120\ipmitool.exe";
  string idracHost = "192.168.4.120", idracUser = "root";
  readonly Label[] gpu = { Value(), Value() }; readonly Label inlet = Value(), exhaust = Value(), status = new Label();
  readonly DataGridView fans = new DataGridView(); readonly Timer timer = new Timer { Interval = 5000 };
  readonly TextBox hostInput = new TextBox(), userInput = new TextBox(), passwordInput = new TextBox(), rpmInput = new TextBox(); string password; bool busy;
  public Monitor() {
    Text="BlackLotus Thermal Monitor"; ClientSize=new Size(720,610); StartPosition=FormStartPosition.CenterScreen; Font=new Font("Segoe UI",9);
    Controls.Add(new Label { Text=Text, Location=new Point(18,14), Size=new Size(680,28), Font=new Font("Segoe UI",16,FontStyle.Bold) });
    Controls.Add(Caption("Monitor only • iDRAC 192.168.4.120 • refreshes every 5 seconds",20,45,670));
    for(int i=0;i<2;i++){int x=22+i*220; Controls.Add(Caption("GPU "+i+" temperature",x,86)); gpu[i].Location=new Point(x,106); Controls.Add(gpu[i]);}
    Controls.Add(Caption("iDRAC inlet",462,86)); inlet.Location=new Point(462,106); Controls.Add(inlet); Controls.Add(Caption("iDRAC exhaust",462,164)); exhaust.Location=new Point(462,184); Controls.Add(exhaust);
    Controls.Add(new Label { Text="iDRAC fan speeds",Location=new Point(22,230),Size=new Size(220,22),Font=new Font("Segoe UI",11,FontStyle.Bold)});
    fans.Location=new Point(22,258); fans.Size=new Size(666,155); fans.ReadOnly=true; fans.AllowUserToAddRows=false; fans.AllowUserToDeleteRows=false; fans.RowHeadersVisible=false; fans.AutoSizeColumnsMode=DataGridViewAutoSizeColumnsMode.Fill; fans.Columns.Add("Fan","Fan"); fans.Columns.Add("Speed","Speed"); fans.Columns.Add("State","Status"); Controls.Add(fans);
    Controls.Add(Caption("iDRAC IP",22,425,120)); hostInput.Text=idracHost; hostInput.Location=new Point(22,445); hostInput.Size=new Size(145,24); Controls.Add(hostInput);
    Controls.Add(Caption("Login ID",177,425,120)); userInput.Text=idracUser; userInput.Location=new Point(177,445); userInput.Size=new Size(105,24); Controls.Add(userInput);
    Controls.Add(Caption("Password",292,425,120)); passwordInput.Location=new Point(292,445); passwordInput.Size=new Size(150,24); passwordInput.UseSystemPasswordChar=true; Controls.Add(passwordInput);
    Controls.Add(Caption("Target fan RPM",452,425,130)); rpmInput.Text="5600"; rpmInput.Location=new Point(452,445); rpmInput.Size=new Size(100,24); Controls.Add(rpmInput);
    var apply=new Button { Text="Apply settings", Location=new Point(562,443), Size=new Size(126,28)}; apply.Click+=async(s,e)=>await ApplySettingsAsync(); Controls.Add(apply);
    status.Location=new Point(22,485); status.Size=new Size(666,42); status.Text="Waiting for first sample…"; Controls.Add(status);
    var refresh=new Button { Text="Refresh now", Location=new Point(590,550), Size=new Size(98,28)}; refresh.Click+=async(s,e)=>await RefreshAsync(); Controls.Add(refresh);
    timer.Tick+=async(s,e)=>await RefreshAsync(); Shown+=async(s,e)=>{password=PasswordBox.Prompt(); if(password==null){Close();return;} passwordInput.Text=password; timer.Start(); await RefreshAsync();}; FormClosed+=(s,e)=>timer.Stop();
  }
  async Task RefreshAsync() {
    if(busy)return; busy=true; status.ForeColor=Color.DimGray; status.Text="Collecting NVIDIA and iDRAC telemetry…";
    try { var n=await Run("nvidia-smi.exe","--query-gpu=index,name,temperature.gpu,power.draw,power.limit --format=csv,noheader,nounits",null); var i=await Run(Ipmi,"-I lanplus -H "+idracHost+" -U "+idracUser+" -E sdr elist full",password); if(n.code!=0||i.code!=0)throw new Exception((n.err+i.err).Trim()); ShowGpus(n.outp); ShowSensors(i.outp); status.ForeColor=Color.ForestGreen; status.Text="Last successful sample: "+DateTime.UtcNow.ToString("o")+" UTC. Monitoring only; fan mode was not changed."; }
    catch(Exception ex){status.ForeColor=Color.Firebrick;status.Text="Sample failed: "+ex.Message;} finally{busy=false;}
  }
  void ShowGpus(string text) { var rows=text.Split(new[]{'\r','\n'},StringSplitOptions.RemoveEmptyEntries).Select(x=>x.Split(',').Select(y=>y.Trim()).ToArray()).ToArray(); for(int x=0;x<2;x++){if(x>=rows.Length){gpu[x].Text="Not detected";continue;} int t; if(!int.TryParse(rows[x][2],out t))throw new Exception("Unexpected NVIDIA temperature data.");gpu[x].Text=t+"°C";gpu[x].ForeColor=t>=90?Color.Firebrick:t>=80?Color.DarkOrange:t>=70?Color.Goldenrod:Color.ForestGreen;} }
  void ShowSensors(string text) { fans.Rows.Clear(); inlet.Text=exhaust.Text="--"; foreach(var line in text.Split(new[]{'\r','\n'},StringSplitOptions.RemoveEmptyEntries)){var p=line.Split('|').Select(x=>x.Trim()).ToArray();if(p.Length!=5)continue;if(p[0].StartsWith("Fan"))fans.Rows.Add(p[0],p[4],p[2]);if(p[0]=="Inlet Temp")inlet.Text=p[4];if(p[0]=="Exhaust Temp")exhaust.Text=p[4];} }
  async Task ApplySettingsAsync() {
    int rpm; if(string.IsNullOrWhiteSpace(hostInput.Text)||string.IsNullOrWhiteSpace(userInput.Text)||string.IsNullOrWhiteSpace(passwordInput.Text)||!int.TryParse(rpmInput.Text,out rpm)||rpm<2800||rpm>9360){MessageBox.Show("Enter an IP address, login ID, password, and a target RPM from 2800 through 9360.",Text,MessageBoxButtons.OK,MessageBoxIcon.Warning);return;}
    int duty=DutyForRpm(rpm); var answer=MessageBox.Show("Apply "+rpm+" RPM (estimated "+duty+"% duty) to all chassis fans on "+hostInput.Text+"? This enables iDRAC manual fan control.",Text,MessageBoxButtons.YesNo,MessageBoxIcon.Warning); if(answer!=DialogResult.Yes)return;
    idracHost=hostInput.Text.Trim(); idracUser=userInput.Text.Trim(); password=passwordInput.Text;
    try { var enable=await Run(Ipmi,"-I lanplus -H "+idracHost+" -U "+idracUser+" -E raw 0x30 0x30 0x01 0x00",password); var set=await Run(Ipmi,"-I lanplus -H "+idracHost+" -U "+idracUser+" -E raw 0x30 0x30 0x02 0xff 0x"+duty.ToString("X2"),password); if(enable.code!=0||set.code!=0)throw new Exception((enable.err+set.err).Trim()); status.ForeColor=Color.DarkOrange;status.Text="Manual fan control active: target "+rpm+" RPM ("+duty+"% duty)."; await RefreshAsync(); } catch(Exception ex){status.ForeColor=Color.Firebrick;status.Text="Could not apply fan setting: "+ex.Message;}
  }
  static int DutyForRpm(int rpm) { int[] r={2800,4400,6400,7200,8700,9360}; int[] d={7,16,30,35,50,53}; for(int i=1;i<r.Length;i++)if(rpm<=r[i])return (int)Math.Round(d[i-1]+(rpm-r[i-1])*(d[i]-d[i-1])/(double)(r[i]-r[i-1]));return d[d.Length-1]; }
  static async Task<Result> Run(string file,string args,string pass){return await Task.Run(()=>{var s=new ProcessStartInfo(file,args){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};if(pass!=null)s.EnvironmentVariables["IPMI_PASSWORD"]=pass;using(var p=Process.Start(s)){var o=p.StandardOutput.ReadToEnd();var e=p.StandardError.ReadToEnd();p.WaitForExit();return new Result{code=p.ExitCode,outp=o,err=e};}});}
  static Label Caption(string s,int x,int y,int w=190){return new Label{Text=s,Location=new Point(x,y),Size=new Size(w,20),ForeColor=Color.DimGray};} static Label Value(){return new Label{Text="--",Size=new Size(190,30),Font=new Font("Segoe UI",15,FontStyle.Bold)};} class Result{public int code;public string outp,err;}
}
class PasswordBox : Form { TextBox input=new TextBox(); PasswordBox(){Text="iDRAC credentials required";ClientSize=new Size(385,140);StartPosition=FormStartPosition.CenterScreen;FormBorderStyle=FormBorderStyle.FixedDialog;Controls.Add(new Label{Text="Enter the iDRAC password. It stays only in this running app.",Location=new Point(18,16),Size=new Size(348,30)});input.Location=new Point(20,53);input.Size=new Size(345,24);input.UseSystemPasswordChar=true;Controls.Add(input);var ok=new Button{Text="Connect",Location=new Point(205,98),DialogResult=DialogResult.OK};Controls.Add(ok);AcceptButton=ok;var cancel=new Button{Text="Cancel",Location=new Point(290,98),DialogResult=DialogResult.Cancel};Controls.Add(cancel);CancelButton=cancel;} public static string Prompt(){using(var d=new PasswordBox())return d.ShowDialog()==DialogResult.OK&&d.input.Text.Length>0?d.input.Text:null;} }
