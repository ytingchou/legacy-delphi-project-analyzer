unit OrderEntry;

interface

uses
  System.SysUtils, Vcl.Forms, uCommon;

type
  TfrmOrderEntry = class(TForm)
  public
    procedure btnSearchClick(Sender: TObject);
  end;

implementation

procedure TfrmOrderEntry.btnSearchClick(Sender: TObject);
var
  SqlText: string;
begin
  SqlText := LoadSql('pricing.xml', 'OrderLookup');
  SqlText := StringReplace(SqlText, ':fPriceCheckRule', 'Y', [rfReplaceAll]);
end;

end.
