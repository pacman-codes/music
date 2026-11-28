import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lain/api.dart';

void main(){
  test('only root HTTPS server URLs',(){
    expect(serverUri('https://example.com/').host,'example.com');
    for(final value in ['http://example.com','https://user:pass@example.com','https://example.com/x','https://example.com?token=1']){
      expect(()=>serverUri(value),throwsFormatException);
    }
  });
  test('native requests use bearer headers and no redirects',()async{
    final api=LainApi(serverUri('https://example.com'),'secret','device',client:MockClient((request)async{
      expect(request.headers['authorization'],'Bearer secret');
      expect(request.followRedirects,isFalse);
      expect(request.url.path,'/v1/tracks');
      expect(request.url.queryParameters['q'],'jungle');
      return http.Response(jsonEncode({'results':[
        {'id':7,'title':'Song','artist':'Artist','playable':true,'liked':false,'rating':9,'decision':'maybe'}
      ],'next_before':null}),200);
    }));
    final rows=api.rows((await api.library(query:'jungle'))['results']);
    expect(rows.single.id,7);
    expect(rows.single.playable,isTrue);
    expect(rows.single.rating,9);
  });
  test('auth failures are not interpreted as successful data',()async{
    final api=LainApi(serverUri('https://example.com'),'secret','device',client:MockClient((_)async=>http.Response('{}',401)));
    await expectLater(api.library(),throwsA(isA<ApiError>().having((e)=>e.status,'status',401)));
  });
}
